"""Communication boundaries for requirements; local mode is explicit and auditable."""
import hashlib
import json
import os
import logging
from pathlib import Path
from .domain.errors import DomainError
from .domain.requirements.document import render_markdown
from .domain.requirements.review import review_input_digest
from .feishu_review_document import FeishuReviewDocumentPublisher,ReviewDocumentError
from .requirements_cards import clarification_card, review_card, progress_card, question_recommendation

logger = logging.getLogger('review_orchestrator_fsm')


def notice_text(task_id, state, target):
    failure = state.recovery.failure if state.recovery else None
    title = '需求工作流执行失败' if target == 'REQ_ENGINE_ERROR' else '需求工作流被阻塞'
    return '\n'.join((title, '任务：'+task_id, '状态：'+target,
        '原因：'+state.reason[:1600],
        '错误码：'+(failure.code if failure else 'UNKNOWN'),
        '恢复条件：'+(failure.unblock_condition if failure else '检查运行日志与配置')))


class RequirementsPrerequisiteError(DomainError):
    error_code = 'PREREQUISITE_MISSING'


class RequirementsTransientError(ConnectionError):
    """Transport-level failure that a later retry can plausibly survive."""
    error_code = 'COMMUNICATION_TRANSIENT'


class LocalRequirementsPort:
    """CLI-only verification checks immutable local bytes; never impersonates cloud QA."""
    def __init__(self, root, store):
        self.root, self.store = Path(root), store

    def publish_notice(self, task_id, state, key, target):
        from .runtime.requirements_effects import immutable_write
        path = self.root / 'requirements' / 'notices' / (hashlib.sha256(key.encode()).hexdigest()+'.txt')
        immutable_write(path, notice_text(task_id,state,target).encode('utf-8'))
        logger.info('REQUIREMENTS_NOTIFY_LOCAL task_id=%s key=%s path=%s', task_id,key,path)
        return {'channel':'local','path':str(path),'delivered':False}

    def publish_progress(self, task_id, key, body):
        from .runtime.requirements_effects import immutable_write
        path = self.root / 'requirements' / 'progress' / (hashlib.sha256(key.encode()).hexdigest()+'.txt')
        immutable_write(path,body.encode('utf-8'))
        logger.info('REQUIREMENTS_PROGRESS_LOCAL task_id=%s key=%s path=%s',task_id,key,path)
        return {'channel':'local','path':str(path),'delivered':False}

    def publish_clarification(self, task_id, state, key):
        from .runtime.requirements_effects import immutable_write
        round_ = state.rounds[-1]
        path = self.root / 'requirements' / 'questions' / (round_.round_id+'.json')
        immutable_write(path,json.dumps({'round':state.to_dict()['rounds'][-1],
            'questions':[q for q in state.to_dict()['questions'] if q['question_id'] in round_.question_ids]},
            ensure_ascii=False,sort_keys=True).encode('utf-8'))

    def publish_review(self, task_id, state, key):
        stamp = review_input_digest(state.draft,state.consensus)
        gate_id = task_id+':review:'+stamp
        self.store.register(gate_id,task_id,{'kind':'local-review','document_digest':state.draft.document_digest,
            'review_input_digest':stamp,'pages':{},'actor_ids':['local-operator']})
        return {'gate_id':gate_id,'cloud_revision_ref':'local:'+state.draft.document_digest}

    def verify_approval(self, task_id, state):
        path = Path(state.review_document_ref)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=state.draft.document_digest:
            raise RequirementsPrerequisiteError('LOCAL_REVIEW_DOCUMENT_CHANGED')
        if not self.store.close_for_approval(state.review_gate_id,state.pending_approval.event_id):
            raise RequirementsPrerequisiteError('APPROVAL_QUEUE_NOT_DRAINED')
        return {'cloud_revision_ref':'local:'+state.draft.document_digest}

    def cancel_task(self, task_id):
        self.store.cancel(task_id)

    def refresh_clarification(self, task_id, state):
        from .runtime.requirements_effects import immutable_write
        from .domain.requirements.model import canonical,digest
        immutable_write(self.root/'requirements'/'answers'/(digest(state.answers)+'.json'),canonical(state.answers).encode('utf-8'))


class FeishuRequirementsPort(LocalRequirementsPort):
    def __init__(self, root, store, *, actor_ids=(), chat_id='', http=None, publisher=None, owner_policy='explicit'):
        super().__init__(root,store)
        from .adapters import FeishuHttpAdapter
        self.actor_ids = tuple(actor_ids)
        if owner_policy not in ('explicit','chat-members'):
            raise ValueError('unsupported decision owner policy')
        self.owner_policy=owner_policy
        self.chat_id = chat_id or os.environ.get('HUMAN_GATE_CHAT_ID','')
        self.app_id = os.environ.get('FEISHU_GATE_APP_ID','')
        self.http = http or FeishuHttpAdapter()
        self.publisher = publisher or FeishuReviewDocumentPublisher(root,strict_readback=True,chat_id=self.chat_id)

    def preflight(self):
        missing = []
        if not self.actor_ids and self.owner_policy == 'explicit':
            missing.append('decision_actor_ids (--decision-actor-id)')
        if not self.chat_id:
            missing.append('HUMAN_GATE_CHAT_ID')
        if not self.app_id:
            missing.append('FEISHU_GATE_APP_ID')
        if not os.environ.get('FEISHU_GATE_APP_SECRET',''):
            missing.append('FEISHU_GATE_APP_SECRET')
        if self.app_id and not self.store.ready(self.app_id):
            missing.append('ready card gateway')
        if missing:
            raise RequirementsPrerequisiteError('DECISION_OWNER_CHAT_OR_READY_CARD_GATEWAY_MISSING: '+', '.join(missing))

    def publish_notice(self, task_id, state, key, target):
        from .adapters import feishu_notifications_enabled
        if not feishu_notifications_enabled():
            logger.info('REQUIREMENTS_NOTIFY_SUPPRESSED task_id=%s key=%s',task_id,key)
            return super().publish_notice(task_id,state,key,target)
        return self._publish_text(task_id,key,notice_text(task_id,state,target),'NOTICE')

    def publish_progress(self, task_id, key, body):
        from .adapters import feishu_notifications_enabled
        if not feishu_notifications_enabled():
            logger.info('REQUIREMENTS_PROGRESS_SUPPRESSED task_id=%s key=%s',task_id,key)
            return super().publish_progress(task_id,key,body)
        if not self.chat_id:
            raise RequirementsPrerequisiteError('HUMAN_GATE_CHAT_ID_MISSING')
        history_gate = task_id + ':progress-status'
        gate_id = task_id + ':progress-event:' + hashlib.sha256(key.encode()).hexdigest()[:24]
        send_key = key
        self.store.register(gate_id,task_id,{'kind':'progress','chat_id':self.chat_id,
                            'app_id':self.app_id,'actor_ids':[],'pages':{}})
        receipt = self.store.send_intent(gate_id,'status',send_key)
        number = key.removeprefix(task_id + ':progress:').partition(':')[0]
        sequence = int(number) if number.isdecimal() else 0
        if receipt['message_id']:
            logger.info('REQUIREMENTS_PROGRESS_REUSED task_id=%s key=%s message_id=%s',task_id,key,receipt['message_id'])
            return {'channel':'feishu','message_id':receipt['message_id'],'delivered':True,'reused':True}
        self._seed_progress_history(task_id,history_gate)
        self.store.remember_progress(history_gate,sequence,body)
        from datetime import datetime, timezone, timedelta
        row = self.store.progress_history(history_gate,sequence)[-1]
        stamp = datetime.fromisoformat(row['occurred_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
        card = progress_card(task_id, body + f'\n\n阶段时间（北京时间）：{stamp:%Y-%m-%d %H:%M:%S}')
        logger.info('REQUIREMENTS_PROGRESS_START task_id=%s key=%s sequence=%s occurred_at=%s',task_id,key,sequence,row['occurred_at'])
        message_id = self._send(gate_id,'status',send_key,card)
        self.store.advance_progress(history_gate,sequence)
        logger.info('REQUIREMENTS_PROGRESS_SUCCESS task_id=%s key=%s message_id=%s',task_id,key,message_id)
        return {'channel':'feishu','message_id':message_id,'delivered':True}

    def _seed_progress_history(self, task_id, gate_id):
        if getattr(self,'_history_seeded_task',None) == task_id:
            return
        path = self.root/'workflow-events.jsonl'
        if path.is_file():
            with path.open(encoding='utf-8') as journal:
                for line in journal:
                    row = json.loads(line)
                    if row.get('task_id') != task_id or row.get('record_type') != 'transition':
                        continue
                    for effect in row.get('effects',[]):
                        request = effect.get('request',{})
                        if request.get('effect_type') != 'requirements_progress':
                            continue
                        payload = request['payload']
                        self.store.remember_progress(gate_id,int(payload['sequence']),payload['body'],row['recorded_at'])
        self._history_seeded_task = task_id

    def refresh_progress(self, task_id):
        raise RequirementsPrerequisiteError('PROGRESS_MESSAGES_IMMUTABLE: publish a distinct stage event instead')

    def _publish_text(self, task_id, key, body, kind):
        if not self.chat_id:
            raise RequirementsPrerequisiteError('HUMAN_GATE_CHAT_ID_MISSING')
        logger.info('REQUIREMENTS_%s_START task_id=%s key=%s',kind,task_id,key)
        result = self.http._request('POST',self.http.base_url+'/open-apis/im/v1/messages?receive_id_type=chat_id',
            {'receive_id':self.chat_id,'msg_type':'text',
             'content':json.dumps({'text':body},ensure_ascii=False),
             'uuid':hashlib.sha256(key.encode()).hexdigest()[:32]},token=self.http._token('gate'))
        message_id = result.get('data',{}).get('message_id','')
        if result.get('code',0) not in (0,'0') or not message_id:
            raise RequirementsPrerequisiteError(kind+'_SEND_NOT_DELIVERED:'+str(result.get('code')))
        logger.info('REQUIREMENTS_%s_SUCCESS task_id=%s key=%s message_id=%s',kind,task_id,key,message_id)
        return {'channel':'feishu','message_id':message_id,'delivered':True}

    def prerequisite_fingerprint(self):
        from .domain.requirements.model import digest
        return digest((self.actor_ids,self.chat_id,self.app_id,self.store.ready(self.app_id),self.owner_policy))

    def cloud_fingerprint(self, source_ref):
        _,version,document_id=source_ref.split(':',2)
        from .feishu_review_document import digest
        return digest(self.publisher._fetch(document_id,int(version))['content'])

    def _document_call(self, method, *args):
        try:
            return method(*args)
        except ReviewDocumentError as error:
            if getattr(error, 'transient', False):
                raise RequirementsTransientError(str(error)) from error
            if (error.source_ref or any(code in str(error) for code in
                ('CLI_FAILED','CLI_REJECTED','CREATE_OUTCOME_UNKNOWN','RECOVERY_METADATA_MISSING'))):
                blocked=RequirementsPrerequisiteError(str(error))
                blocked.recovery_source_ref=error.source_ref or 'communication'
                blocked.prerequisite_fingerprint=error.prerequisite_fingerprint or self.prerequisite_fingerprint()
                raise blocked from error
            raise

    def _send(self, gate_id, page_id, key, card):
        from .adapters import feishu_notifications_enabled
        if not feishu_notifications_enabled():
            raise RequirementsPrerequisiteError('FEISHU_EXTERNAL_DISABLED_FOR_TESTS')
        receipt = self.store.send_intent(gate_id,page_id,key)
        if receipt['message_id']:
            return receipt['message_id']
        if not self.store.begin_send(gate_id,page_id):
            raise RequirementsPrerequisiteError('COMMUNICATION_OUTCOME_UNKNOWN: bind a verified card message receipt before retrying')
        # Platform UUID is bound to the durable send intent; retries cannot invent another card.
        uuid = hashlib.sha256(key.encode()).hexdigest()[:32]
        result = self.http._request('POST',self.http.base_url+'/open-apis/im/v1/messages?receive_id_type=chat_id',
            {'receive_id':self.chat_id,'msg_type':'interactive','content':json.dumps(card,ensure_ascii=False),'uuid':uuid},
            token=self.http._token('gate'))
        if result.get('code',0):
            self.store.send_rejected(gate_id,page_id)
            raise RequirementsPrerequisiteError('CARD_SEND_REJECTED:'+str(result.get('code')))
        message = result.get('data',{}).get('message_id','')
        self.store.delivered(gate_id,page_id,message)
        return message

    def publish_clarification(self, task_id, state, key):
        self.preflight()
        round_ = state.rounds[-1]
        by_id = {q.question_id:q for q in state.questions}
        chunks = [round_.question_ids[n:n+5] for n in range(0,len(round_.question_ids),5)]
        pages = {page:{'send_key':key+':'+page,'questions':{
            'q'+str(i):{'question_id':qid,'revision':by_id[qid].revision,
                        'recommendation':question_recommendation(by_id[qid])} for i,qid in enumerate(chunk)}}
            for page,chunk in zip(round_.page_ids,chunks)}
        gate_id = task_id+':clarification:'+round_.round_id
        token = self.store.register(gate_id,task_id,{'kind':'clarification','round_id':round_.round_id,
            'actor_ids':list(self.actor_ids),'owner_policy':self.owner_policy,'chat_id':self.chat_id,'pages':pages})
        for n,(page,chunk) in enumerate(zip(round_.page_ids,chunks),1):
            self._send(gate_id,page,pages[page]['send_key'],clarification_card(gate_id,token,page,
                [by_id[qid] for qid in chunk],n,len(chunks),state.answers,
                batch_total=len(round_.question_ids),batch_answered=self._batch_answered(state,round_),
                followup=len(state.rounds)>1))

    @staticmethod
    def _batch_answered(state, round_):
        revisions = {q.question_id:q.revision for q in state.questions}
        return len({a.question_id for a in state.answers if a.question_id in round_.question_ids
                    and a.question_revision == revisions[a.question_id]})

    def publish_review(self, task_id, state, key):
        self.preflight()
        draft = state.draft
        receipt = self._document_call(self.publisher.publish,task_id,draft.version,render_markdown(draft),draft.document_digest)
        stamp = review_input_digest(draft,state.consensus)
        gate_id = task_id+':review:'+stamp
        token = self.store.register(gate_id,task_id,{'kind':'review','document_digest':draft.document_digest,
            'review_input_digest':stamp,'actor_ids':list(self.actor_ids),'owner_policy':self.owner_policy,'chat_id':self.chat_id,
            'pages':{'review':{'send_key':key,'questions':{}}}})
        self._send(gate_id,'review',key,review_card(gate_id,token,receipt['url'],draft.version,draft.document_digest))
        return {'gate_id':gate_id,'cloud_revision_ref':receipt['cloud_content_hash'],'url':receipt['url']}

    def refresh_clarification(self, task_id, state, *, upgrade=False):
        from .adapters import feishu_notifications_enabled
        if not feishu_notifications_enabled():
            raise RequirementsPrerequisiteError('FEISHU_EXTERNAL_DISABLED_FOR_TESTS')
        round_ = state.rounds[-1]
        gate_id = task_id+':clarification:'+round_.round_id
        gate = self.store.gate(gate_id)
        by_id = {q.question_id:q for q in state.questions}
        for n,page in enumerate(round_.page_ids,1):
            desc = gate['descriptor']['pages'][page]
            receipt = self.store.send_intent(gate_id,page,desc['send_key'])
            if not receipt['message_id']:
                raise RequirementsPrerequisiteError('CARD_RECEIPT_MISSING')
            questions = [by_id[q['question_id']] for q in desc['questions'].values()]
            answered_ids = {a.question_id for a in state.answers if any(
                a.question_id == q.question_id and a.question_revision == q.revision for q in questions)}
            if not upgrade and len(answered_ids) < len(questions):
                logger.info('CARD_REFRESH_PRESERVED task_id=%s page_id=%s reason=UNSUBMITTED_INPUTS',task_id,page)
                continue
            card = clarification_card(gate_id,gate['token'],page,questions,n,len(round_.page_ids),state.answers,
                batch_total=len(round_.question_ids),batch_answered=self._batch_answered(state,round_),
                followup=len(state.rounds)>1)
            result = self.http._request('PATCH',self.http.base_url+'/open-apis/im/v1/messages/'+receipt['message_id'],
                {'content':json.dumps(card,ensure_ascii=False)},token=self.http._token('gate'))
            if result.get('code',0):
                raise RequirementsPrerequisiteError('CARD_UPDATE_REJECTED')

    def verify_approval(self, task_id, state):
        self.preflight()
        self._document_call(self.publisher.verify,state.draft.version,state.draft.document_digest)
        if not self.store.close_for_approval(state.review_gate_id,state.pending_approval.event_id):
            raise RequirementsPrerequisiteError('APPROVAL_QUEUE_NOT_DRAINED')
        receipt = self.publisher.receipt(state.draft.version)
        return {'cloud_revision_ref':receipt['cloud_content_hash']}

    def verify_message(self, message_id, gate):
        # Used solely for a callback that arrived before the send receipt was saved.
        if not message_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in message_id):
            return False
        result = self.http._request('GET',self.http.base_url+'/open-apis/im/v1/messages/'+message_id,token=self.http._token('gate'))
        if result.get('code',0) not in (0,'0'):
            return False
        items = result.get('data',{}).get('items',[])
        return any(i.get('message_id') == message_id and not i.get('deleted',False)
            and i.get('msg_type') == 'interactive'
            and i.get('chat_id') == self.chat_id and i.get('sender',{}).get('id') == self.app_id
            and i.get('sender',{}).get('sender_type') == 'app'
            and card_binding_present(i.get('body',{}).get('content',''),gate) for i in items)


def card_binding_present(content, gate):
    try:
        root = json.loads(content)
    except (ValueError,TypeError):
        return False
    def visit(value):
        if isinstance(value,dict):
            if (value.get('gate_id')==gate['gate_id'] and value.get('token')==gate['token']
                and value.get('page_id')==gate.get('verification_page_id')):
                return True
            return any(visit(child) for child in value.values())
        return isinstance(value,list) and any(visit(child) for child in value)
    return visit(root)
