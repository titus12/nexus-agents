from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from requirements_test_support import make_state, question
from orchestrator.domain.requirements.model import AssumptionRecord
from orchestrator.domain.requirements.consensus import active_decisions
from orchestrator.domain.requirements.questions import cover_blocking_assumptions, open_round, accept_answers
from orchestrator.requirements_cards import clarification_card
from orchestrator.requirements_gateway import GatewayStore
from orchestrator.requirements_port import FeishuRequirementsPort, RequirementsPrerequisiteError


class BatchingTests(unittest.TestCase):
    def test_answers_to_different_questions_on_one_topic_coexist(self):
        state = make_state()
        q1 = replace(question('q-000001'), topic_key='scope', question='范围怎么定？')
        q2 = replace(question('q-000002'), topic_key='scope', question='约束怎么定？')
        state = replace(state, questions=(q1, q2))
        state = open_round(state, 'REQ_ANALYZE')
        state = accept_answers(state, {'round_id': state.rounds[-1].round_id, 'actor_id': 'local-operator',
            'event_id': 'answers-1', 'answers': [
                {'question_id': 'q-000001', 'question_revision': 1, 'option_ids': ['manual']},
                {'question_id': 'q-000002', 'question_revision': 1, 'option_ids': ['automatic']}]})
        scope = [d for d in active_decisions(state) if d.topic_key == 'scope']
        self.assertEqual(len(scope), 2)
        self.assertEqual({d.supersedes_ids for d in scope}, {()})

    def test_re_answered_question_supersedes_only_its_own_decision(self):
        state = make_state()
        q1 = replace(question('q-000001'), topic_key='scope', question='范围怎么定？')
        q2 = replace(question('q-000002'), topic_key='scope', question='约束怎么定？')
        state = replace(state, questions=(q1, q2))
        state = open_round(state, 'REQ_ANALYZE')
        state = accept_answers(state, {'round_id': state.rounds[-1].round_id, 'actor_id': 'local-operator',
            'event_id': 'answers-1', 'answers': [
                {'question_id': 'q-000001', 'question_revision': 1, 'option_ids': ['manual']},
                {'question_id': 'q-000002', 'question_revision': 1, 'option_ids': ['automatic']}]})
        state = replace(state, questions=(replace(q1, revision=2), q2))
        state = open_round(state, 'REQ_ANALYZE')
        self.assertEqual(state.rounds[-1].question_ids, ('q-000001',))
        state = accept_answers(state, {'round_id': state.rounds[-1].round_id, 'actor_id': 'local-operator',
            'event_id': 'answers-2', 'answers': [
                {'question_id': 'q-000001', 'question_revision': 2, 'option_ids': ['automatic']}]})
        scope = [d for d in active_decisions(state) if d.topic_key == 'scope']
        self.assertEqual(len(scope), 2)
        first = next(d for d in state.decisions if d.conclusion == '由业务触发')
        rev2 = next(d for d in state.decisions if first.decision_id in d.supersedes_ids)
        self.assertEqual(rev2.conclusion, '自动重试')
        self.assertEqual({d.decision_id for d in scope},
                         {rev2.decision_id, next(d.decision_id for d in scope if d.decision_id != rev2.decision_id)})
        self.assertTrue(any(d.topic_key == 'scope' and d.conclusion == '自动重试' and d.decision_id != rev2.decision_id
                            for d in scope))

    def test_rejected_synthesized_constraint_cannot_be_reintroduced_by_model(self):
        from orchestrator.domain.requirements.consensus import apply_analysis
        state = make_state()
        assumption = AssumptionRecord('a','要求兼容旧协议',blocking=True)
        state = cover_blocking_assumptions(replace(state,
            consensus=replace(state.consensus,assumptions=(assumption,))))
        state = open_round(state,'REQ_ANALYZE')
        q = state.questions[-1]
        state = accept_answers(state,{'round_id':state.rounds[-1].round_id,'actor_id':'local-operator',
            'event_id':'explicit-alternative','answers':[{'question_id':q.question_id,'question_revision':1,
                'option_ids':['other'],'free_text':'只支持新协议版本，不承诺旧协议兼容'}]})
        self.assertEqual(state.consensus.assumptions[0].disposition,'rejected')
        with self.assertRaisesRegex(ValueError,'USER_REJECTED_ASSUMPTION_PROTECTED'):
            apply_analysis(state,{'assumptions':[asdict(assumption)]})

    def test_real_app_publishes_one_round_with_known_constraint_and_accepts_batch_in_parts(self):
        from orchestrator.app import OrchestratorApp
        from test_requirements_workflow_e2e import context, Scripted, pump
        class OmittingAnalyst(Scripted):
            def dispatch(self, request):
                receipt = super().dispatch(request)
                if request.target_state == 'REQ_ANALYZE':
                    payload = self.replies[request.request_id][-1].payload
                    payload['questions'] = json.loads(json.dumps([
                        asdict(question()),
                        asdict(replace(question('q-000002'), topic_key='compatibility',
                                       question='是否需要兼容旧版本客户端？'))]))
                    payload['analysis']['assumptions'] = json.loads(json.dumps([asdict(
                        AssumptionRecord('assumption-000001','数据协议版本',blocking=True))]))
                elif request.target_state == 'REQ_DRAFT':
                    # Draft-first: the assumption question appears only after the
                    # draft; bind answered claims so the draft validates.
                    payload = self.replies[request.request_id][-1].payload
                    consensus = request.context['consensus']
                    for section in payload['document']['sections']:
                        if section['kind'] in ('behavior','acceptance'):
                            section['requirement_ids'] = [c['requirement_id'] for c in consensus['claims'] if c['status']=='active']
                            section['markdown'] += ' '.join(c['statement'] for c in consensus['claims'] if c['status']=='active')
                        if section['kind'] == 'behavior':
                            section['decision_ids'] = consensus['active_decision_ids']
                return receipt
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(),directory,multica=OmittingAnalyst(),poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CLARIFY_WAIT')
            req = app.ctx.requirements
            self.assertEqual(len(req.rounds),1)
            self.assertEqual(len(req.rounds[0].question_ids),2)
            rows = [{'question_id':q.question_id,'question_revision':q.revision,
                     'option_ids':['confirm_assumption'] if q.default_assumption_id else ['manual']}
                    for q in req.questions]
            app.requirements_runtime.submit('ANSWERS_SUBMITTED',{'round_id':req.rounds[0].round_id,
                'actor_id':'local-operator','answers':rows[:1]},'partial-answer')
            app.step()
            self.assertEqual(app.ctx.progression.state,'REQ_CLARIFY_WAIT')
            progress = [e.request.payload['body'] for e in app.repository.pending_effects(app.ctx.identity.task_id)
                        if e.request.effect_type == 'requirements_progress']
            self.assertTrue(any('共 2 题，已回答 1 题' in body for body in progress))
            app.requirements_runtime.submit('ANSWERS_SUBMITTED',{'round_id':req.rounds[0].round_id,
                'actor_id':'local-operator','answers':rows[1:]},'batch-answer')
            pump(app,'REQ_ANALYZE')
            self.assertEqual(app.ctx.requirements.rounds[0].status,'answered')
            # Draft-first (D6): the blocking assumption turns into a question
            # only after the first draft exists, and confirming it accepts it.
            pump(app,'REQ_CLARIFY_WAIT')
            req = app.ctx.requirements
            self.assertEqual(req.draft.version,2)
            self.assertEqual(req.rounds[-1].return_to,'REQ_DRAFT')
            self.assertTrue(any(q.default_assumption_id == 'assumption-000001' for q in req.questions))
            app.requirements_runtime.submit('ANSWERS_SUBMITTED',{'round_id':req.rounds[-1].round_id,
                'actor_id':'local-operator','answers':[{'question_id':req.rounds[-1].question_ids[0],
                    'question_revision':1,'option_ids':['confirm_assumption']}]},'assumption-answer')
            pump(app,'REQ_REVIEW')
            self.assertEqual(app.ctx.requirements.consensus.assumptions[0].disposition,'accepted')

    def test_known_omitted_constraint_is_in_first_batch_and_explicit_choice_resolves_it(self):
        state = make_state()
        assumption = AssumptionRecord('assumption-000001', '编码输出必须字节级兼容', blocking=True)
        state = replace(state, questions=(question(),),
                        consensus=replace(state.consensus, assumptions=(assumption,)))
        state = cover_blocking_assumptions(state)
        self.assertEqual(cover_blocking_assumptions(state), state)
        state = open_round(state, 'REQ_ANALYZE')
        self.assertEqual(len(state.rounds[-1].question_ids), 2)
        generated = state.questions[-1]
        state = accept_answers(state, {'round_id':state.rounds[-1].round_id,'actor_id':'local-operator',
            'event_id':'confirmed','answers':[{'question_id':generated.question_id,'question_revision':1,
                                              'option_ids':['confirm_assumption']}]})
        self.assertEqual(state.consensus.assumptions[0].disposition, 'accepted')
        self.assertEqual(len(cover_blocking_assumptions(state).questions), 2)

    def test_existing_assumption_question_and_resolved_assumptions_are_not_duplicated(self):
        state = make_state()
        state = replace(state, questions=(replace(question(),default_assumption_id='a'),),
            consensus=replace(state.consensus, assumptions=(
                AssumptionRecord('a','covered',blocking=True),
                AssumptionRecord('b','resolved',blocking=True,disposition='accepted'))))
        self.assertEqual(cover_blocking_assumptions(state), state)

    def test_grouping_preserves_dependencies_and_card_field_indices(self):
        state = replace(make_state(), questions=(replace(question(),topic_key='scope'),
            replace(question('q-000002'),topic_key='compatibility'),
            replace(question('q-000003'),topic_key='scope'),
            question('q-000004',('q-000001',))))
        state = open_round(state,'REQ_DRAFT')
        self.assertEqual(state.rounds[-1].question_ids, ('q-000001','q-000003','q-000002'))
        by_id = {q.question_id:q for q in state.questions}
        card = clarification_card('gate','token','page',[by_id[i] for i in state.rounds[-1].question_ids],
                                  1,1,batch_total=3,batch_answered=1,followup=True)
        encoded = json.dumps(card,ensure_ascii=False)
        self.assertIn('共 3 题，已回答 1 题',encoded)
        self.assertIn('追加原因',encoded)
        self.assertIn('兼容性',encoded)
        fields = [e['name'] for e in card['body']['elements'][1]['elements'] if e.get('tag')=='input']
        self.assertEqual(fields, ['q0_text','q1_text','q2_text'])

    def test_progress_preserves_independent_cards_across_restart_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            http = Mock(base_url='https://example.invalid')
            http._request.side_effect = [{'code':0,'data':{'message_id':message}} for message in ('om_start','om_draft')]
            store = GatewayStore(Path(directory)/'gateway.sqlite')
            port = FeishuRequirementsPort(directory,store,chat_id='chat',http=http,publisher=Mock())
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                first = port.publish_progress('task','task:progress:1:REQ_ANALYZE','正在分析')
                restarted = FeishuRequirementsPort(directory,GatewayStore(store.path),chat_id='chat',http=http,publisher=Mock())
                second = restarted.publish_progress('task','task:progress:2:REQ_DRAFT','正在编写')
                replay = restarted.publish_progress('task','task:progress:2:REQ_DRAFT','正在编写')
                old = restarted.publish_progress('task','task:progress:1:REQ_ANALYZE','旧进度')
            self.assertNotEqual(first['message_id'],second['message_id'])
            self.assertEqual(replay['message_id'],second['message_id'])
            self.assertEqual(old['message_id'],first['message_id'])
            self.assertEqual([c.args[0] for c in http._request.call_args_list],['POST','POST'])
            self.assertEqual(http._request.call_args_list[0].args[2]['msg_type'],'interactive')
            self.assertNotEqual(http._request.call_args_list[0].args[2]['uuid'],http._request.call_args_list[1].args[2]['uuid'])
            updated=json.loads(http._request.call_args_list[1].args[2]['content'])
            text=updated['body']['elements'][0]['content']
            self.assertNotIn('正在分析',text)
            self.assertIn('正在编写',text)
            self.assertNotIn('推进记录',text)
            original=json.loads(http._request.call_args_list[0].args[2]['content'])['body']['elements'][0]['content']
            self.assertIn('正在分析',original)
            self.assertNotIn('正在编写',original)
            self.assertNotIn('旧进度',text)
            self.assertEqual(len(store.progress_history('task:progress-status',2)),2)

    def test_existing_progress_card_backfills_original_times_from_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            http=Mock(base_url='https://example.invalid')
            http._request.side_effect=[{'code':0,'data':{'message_id':message}} for message in ('om_start','om_wait')]
            store=GatewayStore(Path(directory)/'gateway.sqlite')
            gate='task:progress-status'
            store.register(gate,'task',{'kind':'progress','pages':{}})
            store.send_intent(gate,'status','task:progress-status:message')
            store.delivered(gate,'status','om_existing')
            store.advance_progress(gate,7)
            rows=[{'record_type':'transition','task_id':'task','recorded_at':stamp,
                'effects':[{'request':{'effect_type':'requirements_progress','payload':{'sequence':seq,'body':body}}}]}
                for seq,stamp,body in ((1,'2026-10-07T09:23:58+00:00','【任务已启动｜正在分析】'),
                                      (7,'2026-10-07T09:31:02+00:00','【等待回答】'))]
            (Path(directory)/'workflow-events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                for _ in range(2):
                    port=FeishuRequirementsPort(directory,GatewayStore(store.path),chat_id='chat',http=http,publisher=Mock())
                    for seq,body in ((1,'【任务已启动｜正在分析】'),(7,'【等待回答】')):
                        port.publish_progress('task',f'task:progress:{seq}:stage',body)
                    with self.assertRaisesRegex(RequirementsPrerequisiteError,'PROGRESS_MESSAGES_IMMUTABLE'):
                        port.refresh_progress('task')
            text=json.loads(http._request.call_args.args[2]['content'])['body']['elements'][0]['content']
            original=json.loads(http._request.call_args_list[0].args[2]['content'])['body']['elements'][0]['content']
            self.assertIn('任务已启动',original)
            self.assertIn('2026-10-07 17:23:58',original)
            self.assertIn('2026-10-07 17:31:02',text)
            self.assertIn('等待回答',text)
            self.assertEqual(store.progress_sequence(gate),7)
            self.assertEqual([call.args[0] for call in http._request.call_args_list],['POST','POST'])

    def test_rejected_stage_does_not_advance_sequence_and_retry_keeps_uuid(self):
        with tempfile.TemporaryDirectory() as directory:
            http = Mock(base_url='https://example.invalid')
            http._request.return_value = {'code':0,'data':{'message_id':'om_status'}}
            store = GatewayStore(Path(directory)/'gateway.sqlite')
            port = FeishuRequirementsPort(directory,store,chat_id='chat',http=http,publisher=Mock())
            with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                port.publish_progress('task','task:progress:1:REQ_ANALYZE','分析')
                http._request.return_value = {'code':999}
                with self.assertRaisesRegex(RequirementsPrerequisiteError,'CARD_SEND_REJECTED'):
                    port.publish_progress('task','task:progress:2:REQ_DRAFT','编写')
                self.assertEqual(store.progress_sequence('task:progress-status'),1)
                http._request.return_value={'code':0,'data':{'message_id':'om_draft'}}
                port.publish_progress('task','task:progress:2:REQ_DRAFT','编写')
            self.assertEqual([c.args[0] for c in http._request.call_args_list],['POST','POST','POST'])
            self.assertEqual(http._request.call_args_list[1].args[2]['uuid'],http._request.call_args_list[2].args[2]['uuid'])
