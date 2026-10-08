from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
from requirements_test_support import make_state,question
from orchestrator.domain.requirements.questions import open_round,accept_answers
from orchestrator.requirements_cards import clarification_card
from orchestrator.requirements_gateway import GatewayStore,GatewayBridge
from orchestrator.requirements_port import FeishuRequirementsPort


class RecommendationSubmissionTests(unittest.TestCase):
    def test_recommendation_answer_reads_as_user_confirmation_not_recommendation(self):
        import requirements_bot_round as bot
        text_q={'question_id':'q-text','revision':1,'blocking':True,'options':(),
                'recommendation':'建议选择"耗时不高于基线"，理由：与目标一致；仍需用户确认该解释。'}
        row=bot.recommendation_answer(text_q,'recommend')
        self.assertEqual(row['disposition'],'answered')
        self.assertTrue(row['free_text'].startswith('用户确认：'),row['free_text'])
        self.assertNotIn('建议选择',row['free_text'])
        plain_q={'question_id':'q-plain','revision':1,'blocking':True,'options':(),
                 'recommendation':'无回退为最低验收'}
        self.assertEqual(bot.recommendation_answer(plain_q,'recommend')['free_text'],
                         '用户确认：无回退为最低验收')

    def test_six_answers_across_two_pages_survive_and_only_complete_pages_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            questions=tuple(replace(question(),question_id=f'q-{i}',topic_key=f'topic-{i}',recommendation=f'建议方案 {i}') for i in range(6))
            state=open_round(replace(make_state(),questions=questions),'REQ_ANALYZE',('ou_owner',))
            round_=state.rounds[-1]
            store=GatewayStore(Path(directory)/'gateway.sqlite')
            pages={page:{'send_key':page,'questions':{f'q{i}':{'question_id':q.question_id,'revision':q.revision,'recommendation':q.recommendation} for i,q in enumerate(questions[n*5:n*5+5])}} for n,page in enumerate(round_.page_ids)}
            token=store.register('gate','task',{'round_id':round_.round_id,'kind':'clarification','actor_ids':['ou_owner'],'chat_id':'chat','pages':pages})
            for page in pages:
                store.send_intent('gate',page,page)
                store.delivered('gate',page,'om_'+page)
            repository=Mock()
            repository.is_event_processed.return_value=False
            repository.pending_domain_events.return_value=()
            bridge=GatewayBridge(store,repository)
            http=Mock(base_url='https://example.invalid')
            http._request.return_value={'code':0}
            port=FeishuRequirementsPort(directory,store,http=http,publisher=Mock())
            # Last page first: only one answer is submitted, and the untouched first page must not be patched.
            for page in reversed(round_.page_ids):
                form={field+'_recommendation':'use_recommendation' for field in pages[page]['questions']}
                if page == round_.page_ids[0]:
                    form['q0_text']='用户明确指定的方案'
                body={'header':{'app_id':'app','event_type':'card.action.trigger','event_id':page},'event':{'operator':{'open_id':'ou_owner'},'context':{'open_chat_id':'chat','open_message_id':'om_'+page},'action':{'value':{'gate_id':'gate','token':token,'page_id':page,'kind':'clarification'},'form_value':form}}}
                store.callback(body,'app')
                repository.load.return_value=SimpleNamespace(context=SimpleNamespace(requirements=state,progression=SimpleNamespace(sequence=1)))
                bridge.poll('task')
                event=repository.append_domain_event.call_args.args[0]
                self.assertEqual(len(event.payload['answers']),len(pages[page]['questions']))
                state=accept_answers(state,{**event.payload,'event_id':event.event_id})
                store.acknowledge(store.next('task')['inbox_id'],'DONE',event.event_id)
                with patch('orchestrator.adapters.feishu_notifications_enabled',return_value=True):
                    # Port binds the actual task gate; test gate is renamed through lookup only.
                    with patch.object(store,'gate',return_value=store.gate('gate')),patch.object(store,'send_intent',side_effect=lambda gate,page,key: {'message_id':'om_'+page}):
                        port.refresh_clarification('task',state)
                if len(state.answers)==1:
                    self.assertEqual(http._request.call_count,1)
                    self.assertTrue(http._request.call_args.args[1].endswith(round_.page_ids[1]))
            self.assertEqual(len(state.answers),6)
            self.assertEqual(state.rounds[-1].status,'answered')
            self.assertEqual({a.free_text for a in state.answers}, {'用户明确指定的方案',*(q.recommendation for q in questions[1:])})

    def test_every_question_has_explicit_recommendation_control_without_preselection(self):
        questions=[question(),replace(question(),question_id='text',options=(),answer_kind='text',recommendation='先采集基线再设定目标')]
        form=clarification_card('gate','token','page',questions,1,1)['body']['elements'][1]['elements']
        controls=[element for element in form if element.get('name','').endswith('_recommendation')]
        self.assertEqual([element['name'] for element in controls],['q0_recommendation','q1_recommendation'])
        self.assertTrue(all('initial_option' not in element for element in controls))
        self.assertIn('仅本页',json.dumps(form,ensure_ascii=False))
