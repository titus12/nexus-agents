"""Feishu JSON 2.0 cards, with question text and stable form names."""
def md(text):
    return {'tag':'markdown','content':text}


def value(gate_id, token, page_id, kind):
    return {'gate_id':gate_id,'token':token,'page_id':page_id,'kind':kind}


def button(label, metadata, *, submit=False, name='submit'):
    result = {'tag':'button','text':{'tag':'plain_text','content':label},'type':'primary','value':metadata}
    if submit:
        result.update(action_type='form_submit',name=name)
    return result


def question_recommendation(question):
    return question.recommendation or (('建议选择：'+question.options[0].label) if question.options else '建议先采集相关基线数据，再确定可量化的目标与验收标准。')


def progress_card(task_id, body, history=()):
    if history:
        from datetime import datetime, timezone, timedelta
        lines = []
        for row in history:
            stamp = datetime.fromisoformat(row['occurred_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
            title = row['body'].splitlines()[0].strip('【】')[:160]
            lines.append(f'- {stamp:%Y-%m-%d %H:%M:%S} · {title}')
        body = body + '\n\n**推进记录（北京时间）**\n' + '\n'.join(lines)
    return {'schema':'2.0','config':{'update_multi':True},
        'header':{'title':{'tag':'plain_text','content':'任务进度｜'+task_id}},
        'body':{'elements':[md(body)]}}


def clarification_card(gate_id, token, page_id, questions, page_number, total, answers=(), *,
                       batch_total=None, batch_answered=None, followup=False):
    form = []
    topic = None
    for index,q in enumerate(questions):
        name = 'q'+str(index)
        if q.topic_key != topic:
            topic = q.topic_key
            label = {'constraints':'约束','scope':'范围','platform':'运行环境','compatibility':'兼容性',
                     'acceptance':'验收标准','goal':'目标'}.get(topic,'相关问题')
            if topic.startswith('assumption:'):
                label = '基本约束'
            form.append(md('### '+label))
        options = '\n'.join(f'- {o.label}：{o.consequence}' for o in q.options)
        form.append(md(f'**{q.question}**\n'+('追加原因：' if followup else '原因：')+f'{q.reason}\n影响：{q.impact}\n{options}'+
            f'\n**推荐建议：{question_recommendation(q)}**'+
            ('\n此问题需要回答后继续。' if q.blocking else '\n请选择或填写答案；未回答的问题可稍后补充。')))
        answer = next((a for a in answers if a.question_id == q.question_id and a.question_revision == q.revision),None)
        if answer:
            labels = [o.label for o in q.options if o.option_id in answer.option_ids]
            status = {'answered':'已回答','defer':'已暂缓','accept_default':'已接受默认假设'}[answer.disposition]
            form.append(md('已记录：'+'；'.join(labels+([answer.free_text] if answer.free_text else [])+[status])))
            continue
        if q.options:
            form.append({'tag':'multi_select_static' if q.answer_kind == 'multi' else 'select_static',
                'name':name,'placeholder':{'tag':'plain_text','content':'请选择，也可以填写自己的答案'},
                'options':[{'text':{'tag':'plain_text','content':o.label},'value':o.option_id} for o in q.options]})
        form.append({'tag':'select_static','name':name+'_recommendation',
            'placeholder':{'tag':'plain_text','content':'快捷回答：采用推荐建议'},
            'options':[{'text':{'tag':'plain_text','content':'采用推荐建议'},'value':'use_recommendation'}]})
        form.append({'tag':'input','name':name+'_text','max_length':1000,'input_type':'multiline_text',
            'placeholder':{'tag':'plain_text','content':'补充或自由回答'}})
    form.append(button(f'提交第 {page_number} 页回答（仅本页）',value(gate_id,token,page_id,'clarification'),submit=True))
    answered = batch_answered if batch_answered is not None else sum(
        any(a.question_id == q.question_id and a.question_revision == q.revision for a in answers) for q in questions)
    count = batch_total if batch_total is not None else len(questions)
    return {'schema':'2.0','header':{'title':{'tag':'plain_text','content':f'中书省需求澄清 {page_number}/{total}'}},
        'body':{'elements':[md(f'本批共 {count} 题，已回答 {answered} 题。本页 {page_number}/{total}，共 {len(questions)} 题。提交仅保存本页，其他页须分别提交；整批完成后继续。'),
            {'tag':'form','name':'answers','elements':form},
            button('取消任务',value(gate_id,token,page_id,'cancel'))]}}


def review_card(gate_id, token, url, version, document_digest):
    page = 'review'
    return {'schema':'2.0','header':{'title':{'tag':'plain_text','content':f'需求草案 v{version} 待确认'}},
        'body':{'elements':[md(f'[阅读完整需求草案]({url})\n当前版本摘要：`{document_digest}`'),
            button('批准这份需求草案',value(gate_id,token,page,'approve')),
            {'tag':'form','name':'feedback_form','elements':[
                {'tag':'input','name':'feedback','required':True,'max_length':1000,'input_type':'multiline_text',
                 'placeholder':{'tag':'plain_text','content':'填写修改意见'}},
                button('提交修改意见',value(gate_id,token,page,'feedback'),submit=True,name='feedback_submit')]},
            button('取消任务',value(gate_id,token,page,'cancel'))]}}
