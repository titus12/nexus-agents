"""Durable callback inbox shared by the WebSocket process and task runtimes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import re
import sqlite3
import time
from .requirements_observability import record, error_code


class GatewayStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS gates (
                  gate_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
                  descriptor TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN');
                CREATE TABLE IF NOT EXISTS messages (
                  gate_id TEXT NOT NULL, page_id TEXT NOT NULL, send_key TEXT UNIQUE NOT NULL,
                  message_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'INTENT',
                  PRIMARY KEY (gate_id,page_id));
                CREATE TABLE IF NOT EXISTS inbox (
                  inbox_id INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT NOT NULL,
                  event_id TEXT NOT NULL, task_id TEXT NOT NULL, gate_id TEXT NOT NULL,
                  payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'RECEIVED',
                  domain_event_id TEXT NOT NULL DEFAULT '', UNIQUE(app_id,event_id));
                CREATE TABLE IF NOT EXISTS closures (
                  gate_id TEXT PRIMARY KEY, approval_event_id TEXT NOT NULL UNIQUE,
                  cutoff INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PREPARED');
                CREATE TABLE IF NOT EXISTS heartbeat (
                  app_id TEXT PRIMARY KEY, updated_at REAL NOT NULL, ready INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS progress_sequences (
                  gate_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS progress_events (
                  gate_id TEXT NOT NULL, sequence INTEGER NOT NULL, body TEXT NOT NULL,
                  occurred_at TEXT NOT NULL, PRIMARY KEY(gate_id,sequence));
            ''')
            if 'started_at' not in {row['name'] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN started_at REAL NOT NULL DEFAULT 0')

    @contextmanager
    def connect(self):
        # Stay below the card callback deadline; on contention fail without ack.
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, gate_id, task_id, descriptor):
        with self.connect() as db:
            old = db.execute('SELECT * FROM gates WHERE gate_id=?', (gate_id,)).fetchone()
            if old:
                if json.loads(old['descriptor']) != descriptor or old['task_id'] != task_id:
                    raise ValueError('GATE_ID_COLLISION')
                return old['token']
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO gates(gate_id,task_id,token,descriptor) VALUES (?,?,?,?)',
                (gate_id,task_id,token,json.dumps(descriptor,ensure_ascii=False,sort_keys=True)))
            return token

    def gate(self, gate_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM gates WHERE gate_id=?',(gate_id,)).fetchone()
        if not row:
            raise ValueError('UNKNOWN_GATE')
        return {**dict(row), 'descriptor':json.loads(row['descriptor'])}

    def send_intent(self, gate_id, page_id, send_key):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO messages(gate_id,page_id,send_key) VALUES (?,?,?)',
                       (gate_id,page_id,send_key))
            row = db.execute('SELECT * FROM messages WHERE gate_id=? AND page_id=?',(gate_id,page_id)).fetchone()
            if row['send_key'] != send_key:
                raise ValueError('SEND_KEY_COLLISION')
            return dict(row)

    def delivered(self, gate_id, page_id, message_id):
        if not message_id:
            raise ValueError('MESSAGE_RECEIPT_REQUIRED')
        with self.connect() as db:
            row = db.execute('SELECT message_id FROM messages WHERE gate_id=? AND page_id=?',(gate_id,page_id)).fetchone()
            if not row or row['message_id'] not in ('',message_id):
                raise ValueError('MESSAGE_RECEIPT_COLLISION')
            db.execute("UPDATE messages SET message_id=?,status='DELIVERED' WHERE gate_id=? AND page_id=?",(message_id,gate_id,page_id))

    def begin_send(self, gate_id, page_id):
        """A lost HTTP outcome cannot create another card on replay."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM messages WHERE gate_id=? AND page_id=?',(gate_id,page_id)).fetchone()
            if not row or row['status'] != 'INTENT':
                return False
            db.execute("UPDATE messages SET status='OUTCOME_UNKNOWN',started_at=? WHERE gate_id=? AND page_id=?",
                       (time.time(),gate_id,page_id))
            return True

    def send_rejected(self, gate_id, page_id):
        with self.connect() as db:
            db.execute("UPDATE messages SET status='INTENT' WHERE gate_id=? AND page_id=? AND message_id=''",(gate_id,page_id))

    def heartbeat(self, app_id, ready):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO heartbeat VALUES (?,?,?)',(app_id,time.time(),int(ready)))

    def ready(self, app_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM heartbeat WHERE app_id=?',(app_id,)).fetchone()
        return bool(row and row['ready'] and time.time()-row['updated_at'] < 60)

    def callback(self, body, expected_app_id):
        header = body.get('header', {}) if isinstance(body, dict) else {}
        event_id = str(header.get('event_id', ''))[:128] if isinstance(header, dict) else ''
        record('CARD_CALLBACK_RECEIVED', event_id=event_id)
        try:
            return self._callback(body, expected_app_id)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            record('CARD_CALLBACK_REJECTED', event_id=event_id, reason=error_code(error))
            raise
        except Exception as error:
            record('CARD_CALLBACK_NOT_ACKNOWLEDGED', event_id=event_id, reason=type(error).__name__)
            raise

    def _callback(self, body, expected_app_id):
        """Persist authenticated SDK delivery before returning any success toast."""
        header, event = body.get('header',{}), body.get('event',{})
        if header.get('app_id') != expected_app_id or header.get('event_type') != 'card.action.trigger' or not header.get('event_id'):
            raise ValueError('CALLBACK_APP_BINDING_INVALID')
        action = event.get('action',{})
        value = action.get('value',{})
        gate = self.gate(value.get('gate_id',''))
        desc = gate['descriptor']
        actor = event.get('operator',{}).get('open_id','')
        ctx = event.get('context',{})
        authorized = actor in desc.get('actor_ids',[])
        if desc.get('owner_policy') == 'chat-members' and not desc.get('actor_ids'):
            authorized = isinstance(actor,str) and re.fullmatch(r'ou_[A-Za-z0-9]+',actor) is not None
        if (not secrets.compare_digest(str(value.get('token','')),gate['token']) or
            not authorized or ctx.get('open_chat_id') != desc.get('chat_id')):
            raise ValueError('CALLBACK_GATE_BINDING_INVALID')
        page = str(value.get('page_id',''))
        if page not in desc.get('pages',{}):
            raise ValueError('CALLBACK_PAGE_INVALID')
        payload = {'kind':value.get('kind'), 'actor_id':actor,'page_id':page,
                   'form_value':action.get('form_value',{}),'message_id':ctx.get('open_message_id','')}
        with self.connect() as db:
            status = db.execute('SELECT status FROM gates WHERE gate_id=?',(gate['gate_id'],)).fetchone()['status']
            # Ordinary feedback arriving after the approval barrier is a follow-up.
            disposition = 'FOLLOWUP' if status == 'CLOSED' and payload['kind'] != 'cancel' else 'RECEIVED'
            cursor = db.execute('INSERT OR IGNORE INTO inbox(app_id,event_id,task_id,gate_id,payload,status) VALUES(?,?,?,?,?,?)',
                (expected_app_id,header['event_id'],gate['task_id'],gate['gate_id'],json.dumps(payload,ensure_ascii=False),disposition))
            inserted = cursor.rowcount == 1
            row = db.execute('SELECT inbox_id,status FROM inbox WHERE app_id=? AND event_id=?',
                             (expected_app_id, header['event_id'])).fetchone()
        record('CARD_CALLBACK_COMMITTED' if inserted else 'CARD_CALLBACK_DUPLICATE',
               event_id=header['event_id'], task_id=gate['task_id'], gate_id=gate['gate_id'],
               page_id=page, inbox_id=row['inbox_id'], status=row['status'],
               message_id=payload['message_id'])
        return {'toast':{'type':'info','content':'已收录，正在核对当前版本'}}

    def local(self, task_id, event_name, payload, event_id):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO inbox(app_id,event_id,task_id,gate_id,payload) VALUES(?,?,?,?,?)',
                ('local-runtime',event_id,task_id,'',json.dumps({'event_name':event_name,'payload':payload},ensure_ascii=False)))

    def next(self, task_id):
        with self.connect() as db:
            row = db.execute("""SELECT * FROM inbox WHERE task_id=? AND status IN ('RECEIVED','BRIDGED')
                ORDER BY CASE WHEN app_id='local-runtime' AND json_extract(payload,'$.event_name')='CANCEL'
                THEN 0 ELSE 1 END, inbox_id LIMIT 1""",(task_id,)).fetchone()
        return {**dict(row),'payload':json.loads(row['payload'])} if row else None

    def acknowledge(self, inbox_id, status, event_id=''):
        with self.connect() as db:
            changed = db.execute('UPDATE inbox SET status=?,domain_event_id=? WHERE inbox_id=? AND (status!=? OR domain_event_id!=?)',
                                 (status,event_id,inbox_id,status,event_id)).rowcount
        if changed:
            record('CARD_INBOX_ACK', inbox_id=inbox_id, status=status, domain_event_id=event_id)

    def diagnostics(self):
        with self.connect() as db:
            counts = {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) AS n FROM inbox GROUP BY status')}
            latest = db.execute('SELECT MAX(inbox_id) FROM inbox').fetchone()[0]
        return {'inbox_counts': counts, 'latest_inbox_id': latest}

    def remember_progress(self, gate_id, sequence, body, occurred_at=''):
        timestamp = occurred_at or datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO progress_events VALUES (?,?,?,?)',
                       (gate_id,sequence,body,timestamp))

    def progress_history(self, gate_id, through_sequence):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT sequence,body,occurred_at FROM progress_events WHERE gate_id=? AND sequence<=? ORDER BY sequence',
                (gate_id,through_sequence))]

    def progress_sequence(self, gate_id):
        with self.connect() as db:
            row = db.execute('SELECT sequence FROM progress_sequences WHERE gate_id=?', (gate_id,)).fetchone()
        return row['sequence'] if row else -1

    def advance_progress(self, gate_id, sequence):
        with self.connect() as db:
            db.execute('INSERT INTO progress_sequences VALUES (?,?) ON CONFLICT(gate_id) '
                       'DO UPDATE SET sequence=MAX(sequence,excluded.sequence)', (gate_id,sequence))

    def close_for_approval(self, gate_id, approval_event_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            gate = db.execute('SELECT task_id FROM gates WHERE gate_id=?',(gate_id,)).fetchone()
            if not gate:
                raise ValueError('UNKNOWN_GATE')
            task_id = gate['task_id']
            old = db.execute('SELECT * FROM closures WHERE gate_id=?',(gate_id,)).fetchone()
            if old and old['approval_event_id'] != approval_event_id:
                raise ValueError('APPROVAL_BARRIER_COLLISION')
            if not old:
                cutoff = db.execute('SELECT COALESCE(MAX(inbox_id),0) FROM inbox WHERE task_id=?',(task_id,)).fetchone()[0]
                db.execute('INSERT INTO closures(gate_id,approval_event_id,cutoff) VALUES (?,?,?)',(gate_id,approval_event_id,cutoff))
                db.execute("UPDATE gates SET status='CLOSED' WHERE task_id=? AND status='OPEN'",(task_id,))
            else:
                cutoff = old['cutoff']
            pending = db.execute("SELECT COUNT(*) FROM inbox WHERE task_id=? AND inbox_id<=? AND status IN ('RECEIVED','BRIDGED') AND domain_event_id!=?",(task_id,cutoff,approval_event_id)).fetchone()[0]
            return not pending

    def cancel(self, task_id):
        with self.connect() as db:
            db.execute("UPDATE gates SET status='CANCELLED' WHERE task_id=?",(task_id,))


class GatewayBridge:
    def __init__(self, store, repository, message_verifier=None):
        self.store, self.repository, self.message_verifier = store, repository, message_verifier
        self._hold_logs = {}

    def poll(self, task_id):
        from .domain.events import DomainEvent
        while (row := self.store.next(task_id)):
            event_id = 'requirements-human:' + str(row['inbox_id'])
            if self.repository.is_event_processed(event_id):
                self.store.acknowledge(row['inbox_id'],'DONE',event_id)
                continue
            if any(e.event_id == event_id for e in self.repository.pending_domain_events(task_id)):
                self.store.acknowledge(row['inbox_id'],'BRIDGED',event_id)
                return True
            context = self.repository.load(task_id).context
            raw = row['payload']
            try:
                if row['app_id'] == 'local-runtime':
                    name, payload = raw['event_name'], raw['payload']
                else:
                    gate = self.store.gate(row['gate_id'])
                    desc = gate['descriptor']
                    page = desc['pages'][raw['page_id']]
                    receipt = self.store.send_intent(row['gate_id'],raw['page_id'],page['send_key'])
                    if not receipt['message_id']:
                        if not self.message_verifier or not self.message_verifier(raw['message_id'], {**gate,'verification_page_id':raw['page_id']}):
                            now = time.monotonic()
                            if now - self._hold_logs.get(row['inbox_id'], -60) >= 60:
                                record('CARD_BRIDGE_WAIT', task_id=task_id, inbox_id=row['inbox_id'],
                                       reason='MESSAGE_RECEIPT_UNVERIFIED', gate_id=row['gate_id'])
                                self._hold_logs[row['inbox_id']] = now
                            return True  # Hold model completion behind authenticated early human input.
                        self.store.delivered(row['gate_id'],raw['page_id'],raw['message_id'])
                    elif receipt['message_id'] != raw['message_id']:
                        raise ValueError('CALLBACK_MESSAGE_MISMATCH')
                    kind, form = raw['kind'], raw['form_value']
                    payload = {'actor_id':raw['actor_id'],'gate_id':row['gate_id']}
                    if desc.get('owner_policy') == 'chat-members' and not desc.get('actor_ids'):
                        payload['_verified_chat_member']=True
                    if kind == 'clarification':
                        name = 'ANSWERS_SUBMITTED'
                        answers = []
                        for field, q in page['questions'].items():
                            selected = form.get(field,[])
                            if isinstance(selected,str):
                                selected = [selected] if selected else []
                            text = form.get(field+'_text','')
                            recommendation = form.get(field+'_recommendation')
                            if recommendation == 'use_recommendation' and not selected and not text.strip():
                                text = q.get('recommendation','')
                                question = next((item for item in context.requirements.questions
                                    if item.question_id == q['question_id'] and item.revision == q['revision']),None)
                                if question is None:
                                    raise ValueError('STALE_QUESTION_REVISION')
                                if not text:
                                    from .requirements_cards import question_recommendation
                                    text = question_recommendation(question)
                                if not question.recommendation and question.options:
                                    selected = [question.options[0].option_id]
                            disposition = form.get(field+'_disposition') or 'answered'
                            if selected or text or disposition != 'answered':
                                answers.append({'question_id':q['question_id'],'question_revision':q['revision'],
                                    'option_ids':selected,'free_text':text,'disposition':disposition})
                        if not answers:
                            raise ValueError('EMPTY_SUBMISSION')
                        record('CARD_FORM_PARSED',task_id=task_id,inbox_id=row['inbox_id'],
                               page_id=raw['page_id'],page_question_count=len(page['questions']),
                               submitted_answer_count=len(answers),form_field_count=len(form))
                        payload.update(round_id=desc['round_id'],answers=answers)
                    elif kind == 'approve':
                        name = 'REQUIREMENTS_APPROVED'
                        payload.update(document_digest=desc['document_digest'],review_input_digest=desc['review_input_digest'])
                    elif kind == 'feedback':
                        name = 'REQUIREMENTS_FEEDBACK'
                        text = str(form.get('feedback','')).strip()
                        if not text or len(text)>1000:
                            raise ValueError('FEEDBACK_LENGTH_INVALID')
                        payload['text'] = text
                    elif kind == 'cancel':
                        name = 'CANCEL'
                    else:
                        raise ValueError('UNKNOWN_CALLBACK_ACTION')
                    if gate['status'] == 'CANCELLED' and kind != 'cancel':
                        raise ValueError('GATE_CANCELLED')
                event = DomainEvent(name,task_id,context.progression.sequence,payload,
                    datetime.now(timezone.utc).isoformat(),event_id)
            except (ValueError,KeyError,TypeError) as error:
                record('CARD_BRIDGE_REJECTED', task_id=task_id, inbox_id=row['inbox_id'],
                       reason=error_code(error), gate_id=row['gate_id'])
                self.store.acknowledge(row['inbox_id'],'REJECTED',event_id)
                continue
            self.repository.append_domain_event(event)
            record('CARD_BRIDGE_QUEUED', task_id=task_id, inbox_id=row['inbox_id'],
                   domain_event_id=event_id, event=name, sequence=event.sequence,
                   answer_count=len(payload.get('answers', [])))
            self.store.acknowledge(row['inbox_id'],'BRIDGED',event_id)
            return True
        return False
