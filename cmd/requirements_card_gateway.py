"""Dedicated official SDK WebSocket entrypoint. Never logs credentials or card bodies."""
import argparse
import json
import logging
import os
import threading
from orchestrator.locks import TaskLock
from orchestrator.requirements_gateway import GatewayStore
from orchestrator.logging_setup import configure_logging
from orchestrator.requirements_observability import record, SafeSdkHandler, error_code, frame_metadata


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--check',action='store_true',help='Read readiness without opening a WebSocket.')
    args = parser.parse_args(argv)
    app_id, secret = os.environ.get('FEISHU_GATE_APP_ID',''),os.environ.get('FEISHU_GATE_APP_SECRET','')
    if args.check:
        import importlib.metadata
        store = GatewayStore(args.database)
        try:
            sdk = importlib.metadata.version('lark-oapi')
        except importlib.metadata.PackageNotFoundError:
            sdk = None
        report = {'sdk_version':sdk,'database_writable':True,
            'process_lock':TaskLock(args.database+'.gateway.lock').has_live_owner(),
            'websocket_ready':store.ready(app_id),'credentials_present':bool(app_id and secret)}
        print(json.dumps(report,ensure_ascii=False))
        return 0 if report['process_lock'] and report['websocket_ready'] else 1
    if not app_id or not secret:
        parser.error('Gate app credentials are required')
    configure_logging()
    # Install only in the dedicated gateway environment documented in runtime instructions.
    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    store = GatewayStore(args.database)
    lock = TaskLock(args.database+'.gateway.lock')
    lock.acquire()
    record('GATEWAY_STARTED', pid=os.getpid(), database=str(store.path))
    stopped = threading.Event()
    def callback(data):
        record('CARD_SDK_CALLBACK_RECEIVED', pid=os.getpid())
        try:
            response = store.callback(json.loads(lark.JSON.marshal(data)),app_id)
        except (ValueError,KeyError,TypeError,AttributeError) as error:
            record('CARD_CALLBACK_ERROR_RESPONSE', reason=error_code(error))
            response = {'toast':{'type':'error','content':'此卡片、操作者或版本无法核对，请查看当前任务'}}
        # SQLite failures propagate: never acknowledge a callback that was not durably written.
        result = P2CardActionTriggerResponse(response)
        record('CARD_CALLBACK_RESPONSE_READY', toast_type=response.get('toast', {}).get('type', ''))
        return result
    handler = lark.EventDispatcherHandler.builder('','').register_p2_card_action_trigger(callback).build()
    class ObservedClient(lark.ws.Client):
        async def _handle_data_frame(self, frame):
            metadata = frame_metadata(frame)
            record('GATEWAY_DATA_FRAME_RECEIVED', **metadata)
            if metadata.get('type') == 'card':
                record('GATEWAY_DATA_FRAME_UNSUPPORTED', reason='SDK_LEGACY_CARD_FRAME', **metadata)
            return await super()._handle_data_frame(frame)

        async def _connect(self):
            try:
                return await super()._connect()
            except Exception as error:
                causes = []
                current = error
                while current is not None and len(causes) < 4:
                    causes.append(type(current).__name__)
                    current = current.__cause__ or current.__context__
                record('GATEWAY_CONNECT_FAILED', error_type=type(error).__name__, causes=causes,
                       errno=getattr(error, 'errno', None),
                       code=(getattr(error, 'code', None) if isinstance(getattr(error, 'code', None), int) else None))
                raise
    client = ObservedClient(app_id,secret,event_handler=handler,log_level=lark.LogLevel.ERROR)
    # Pinned SDK connection state is inspected only here; thread liveness does not mean connected.
    sdk_logger = logging.getLogger('Lark')
    sdk_logger.disabled = False
    sdk_logger.setLevel(logging.WARNING)
    sdk_logger.handlers = [SafeSdkHandler()]
    sdk_logger.propagate = False
    def heartbeat():
        previous_ready = None
        tick = 0
        while not stopped.wait(10):
            try:
                connection = getattr(client,'_conn',None)
                ready = connection is not None and (getattr(connection,'open',False) or getattr(connection,'state',None) == 1)
                store.heartbeat(app_id,ready)
                lock.refresh()
                if ready != previous_ready:
                    record('GATEWAY_CONNECTION_CHANGED', pid=os.getpid(), ready=bool(ready))
                    previous_ready = ready
                if tick % 6 == 0:
                    record('GATEWAY_HEALTH', pid=os.getpid(), ready=bool(ready), **store.diagnostics())
                tick += 1
            except Exception as error:
                record('GATEWAY_HEARTBEAT_FAILED', pid=os.getpid(), reason=type(error).__name__)
                return
    thread = threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    try:
        client.start()
    except Exception as error:
        record('GATEWAY_STOPPED_WITH_ERROR', pid=os.getpid(), reason=type(error).__name__)
        raise
    finally:
        stopped.set()
        store.heartbeat(app_id,False)
        lock.release()
        record('GATEWAY_STOPPED', pid=os.getpid())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
