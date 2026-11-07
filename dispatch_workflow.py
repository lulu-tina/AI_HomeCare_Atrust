"""Unified local dispatch follow-up and availability. No external messages are sent."""
CAREFLOW_BUILD = '2026.10.18-v19'
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

STATES = ['建議', '督導確認', '待回覆', '已接單', '已通知案家']

def stamp():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')

def task_signature(task, cg):
    values = [str(task.get(k, '')) for k in ['任務ID','案家ID','日期','時間窗_開始','時間窗_結束']]
    return hashlib.sha256(json.dumps(values + [str(cg or '')], ensure_ascii=False).encode()).hexdigest()

def overlap(task, interval):
    try:
        day = pd.Timestamp(task.get('日期')).date().isoformat()
        start = pd.Timestamp(day + ' ' + str(task['時間窗_開始'])[:5])
        end = pd.Timestamp(day + ' ' + str(task['時間窗_結束'])[:5])
        return start < pd.Timestamp(interval['end']) and end > pd.Timestamp(interval['start'])
    except (ValueError, TypeError, KeyError):
        return False

def blocked_reason(task, cg):
    blocks = cg.get('臨時不可排時段', [])
    if isinstance(blocks, str):
        try: blocks = json.loads(blocks)
        except ValueError: return '臨時不可排時段資料格式錯誤'
    if not isinstance(blocks, list): return None
    for item in blocks:
        if overlap(task, item):
            return f"臨時不可排：{item['start']}～{item['end']}（{item.get('reason', '請假')}）"
    return None

class WorkflowStore:
    def __init__(self, scope, path='output_results/dispatch_workflow.sqlite3'):
        self.scope = hashlib.sha256(str(scope).encode()).hexdigest()
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records(scope TEXT, task TEXT, body TEXT, PRIMARY KEY(scope,task))')
            db.execute('CREATE TABLE IF NOT EXISTS blocks(id INTEGER PRIMARY KEY, scope TEXT, cg TEXT, start TEXT, end TEXT, reason TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS pending(scope TEXT, task TEXT, body TEXT, PRIMARY KEY(scope,task))')
            db.execute('CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, scope TEXT, task TEXT, at TEXT, body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS client_primary(scope TEXT, client TEXT, cg TEXT, updated TEXT, PRIMARY KEY(scope,client))')
            db.execute('CREATE TABLE IF NOT EXISTS workdays(scope TEXT, cg TEXT, date TEXT, PRIMARY KEY(scope,cg,date))')
    def connect(self): return sqlite3.connect(self.path, timeout=15)
    def primary_map(self):
        with self.connect() as db:
            return dict(db.execute('SELECT client,cg FROM client_primary WHERE scope=?',(self.scope,)).fetchall())
    def save_primary_map(self, mapping):
        """Save reviewed client owners; backups never become owners implicitly."""
        with self.connect() as db:
            for client,cg in mapping.items():
                if not str(client).strip() or not str(cg).strip():
                    raise ValueError('固定案家與居服員編號不可空白。')
                old=db.execute('SELECT cg FROM client_primary WHERE scope=? AND client=?',(self.scope,str(client))).fetchone()
                if old and old[0]!=str(cg):
                    raise ValueError(f'案家 {client} 已有固定居服員 {old[0]}，代班不能取代固定人選。')
                db.execute('INSERT OR REPLACE INTO client_primary VALUES(?,?,?,?)',(self.scope,str(client),str(cg),stamp()))
            db.execute('INSERT INTO history(scope,task,at,body) VALUES(?,?,?,?)',(self.scope,'client_primary',stamp(),json.dumps(mapping,ensure_ascii=False)))
    def get(self, tid):
        with self.connect() as db:
            row=db.execute('SELECT body FROM records WHERE scope=? AND task=?',(self.scope,str(tid))).fetchone()
        return json.loads(row[0]) if row else {}
    def all_records(self):
        with self.connect() as db:
            return {tid:json.loads(body) for tid,body in db.execute('SELECT task,body FROM records WHERE scope=?',(self.scope,))}
    def set_reviewed_primary(self, client, cg, reason):
        """Explicit supervisor whole-client choice, never an automatic backup."""
        if not str(client).strip() or not str(cg).strip() or not reason.strip():
            raise ValueError('請提供案家、固定居服員與整案調整原因。')
        with self.connect() as db:
            old=db.execute('SELECT cg FROM client_primary WHERE scope=? AND client=?',(self.scope,str(client))).fetchone()
            db.execute('INSERT OR REPLACE INTO client_primary VALUES(?,?,?,?)',(self.scope,str(client),str(cg),stamp()))
            body={'client':str(client),'old':old[0] if old else '', 'new':str(cg),'reason':reason,'kind':'supervisor_whole_client'}
            db.execute('INSERT INTO history(scope,task,at,body) VALUES(?,?,?,?)',(self.scope,'client_primary',stamp(),json.dumps(body,ensure_ascii=False)))
    def save(self, tid, record):
        record = {**record, 'updated':stamp()}
        body=json.dumps(record,ensure_ascii=False,default=str)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(self.scope,str(tid),body))
            db.execute('INSERT INTO history(scope,task,at,body) VALUES(?,?,?,?)',(self.scope,str(tid),stamp(),body))
    def record(self, task, cg, confirmed=False, cached=None):
        old=self.get(task['任務ID']) if cached is None else cached
        sig=task_signature(task,cg)
        if old.get('signature')==sig:return old
        # Replies apply only to the exact person/date/time, never to a changed assignment.
        record = {'signature':sig,'cg':cg or '', 'state':('督導確認' if confirmed else '建議') if cg else '待安排',
                'owner':old.get('owner',''),'due':old.get('due',''),'note':old.get('note',''), 'previous_state':old.get('state','')}
        if old:self.save(task['任務ID'],record)
        return record
    def blocks(self):
        with self.connect() as db:
            rows=db.execute('SELECT id,cg,start,end,reason FROM blocks WHERE scope=? ORDER BY start',(self.scope,)).fetchall()
        return [dict(zip(['id','cg','start','end','reason'],r)) for r in rows]
    def add_blocks(self, cg, day, spans, reason):
        if not reason.strip():raise ValueError('請填寫不可排原因。')
        items=[]
        for start,end in spans:
            a=pd.Timestamp(f'{day} {start}');b=pd.Timestamp(f'{day} {end}')
            if b<=a:raise ValueError('每段結束時間必須晚於開始時間；跨日請分日期登錄。')
            items.append((a,b))
        if not items:raise ValueError('請至少填入一段時段。')
        merged=[]
        for a,b in sorted(items):
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
            else:merged.append((a,b))
        with self.connect() as db:
            for a,b in merged:
                data=(self.scope,str(cg),a.isoformat(),b.isoformat(),reason.strip())
                if not db.execute('SELECT 1 FROM blocks WHERE scope=? AND cg=? AND start=? AND end=? AND reason=?',data).fetchone():
                    db.execute('INSERT INTO blocks(scope,cg,start,end,reason) VALUES(?,?,?,?,?)',data)
            db.execute('INSERT INTO history(scope,task,at,body) VALUES(?,?,?,?)',(self.scope,'availability',stamp(),json.dumps({'cg':cg,'spans':[(str(a),str(b)) for a,b in merged],'reason':reason},ensure_ascii=False)))
    def remove_block(self, block_id):
        with self.connect() as db:
            row=db.execute('SELECT cg,start,end,reason FROM blocks WHERE scope=? AND id=?',(self.scope,block_id)).fetchone()
            db.execute('DELETE FROM blocks WHERE scope=? AND id=?',(self.scope,block_id))
            db.execute('INSERT INTO history(scope,task,at,body) VALUES(?,?,?,?)',(self.scope,'availability',stamp(),json.dumps({'removed':row},ensure_ascii=False)))
    def pending(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM pending WHERE scope=?',(self.scope,))]
    def save_pending(self, task):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO pending VALUES(?,?,?)',(self.scope,str(task['任務ID']),json.dumps(task,ensure_ascii=False,default=str)))
    def remove_pending(self, tid):
        with self.connect() as db:
            db.execute('DELETE FROM pending WHERE scope=? AND task=?',(self.scope,str(tid)))
    def history(self, tid):
        with self.connect() as db:
            return db.execute('SELECT at,body FROM history WHERE scope=? AND task=? ORDER BY id DESC LIMIT 20',(self.scope,str(tid))).fetchall()
    def record_workdays(self, pairs):
        """pairs：(居服員ID, 日期) 的可疊代序列，寫入本機構已確定的上班日。scope 以 institution_key
        為準、不隨月份變動，供下一次排班（含下個月）查「連續上班天數」時跨月回溯使用。"""
        rows = {(str(cg).strip(), str(day)[:10]) for cg, day in pairs if str(cg).strip() and str(day).strip()}
        if not rows:
            return
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO workdays VALUES(?,?,?)', [(self.scope, cg, day) for cg, day in rows])
    def recent_workdays(self, before_date, lookback=10):
        """回傳 {居服員ID: {日期字串,...}}：before_date 之前 lookback 天內、本機構已確定的上班日，
        作為新一輪排班「連續上班天數」判斷的跨月種子。before_date 當天不含在內。"""
        before = pd.Timestamp(before_date).strftime('%Y-%m-%d')
        start = (pd.Timestamp(before_date) - pd.Timedelta(days=lookback)).strftime('%Y-%m-%d')
        with self.connect() as db:
            rows = db.execute('SELECT cg,date FROM workdays WHERE scope=? AND date>=? AND date<?',
                               (self.scope, start, before)).fetchall()
        out = {}
        for cg, day in rows:
            out.setdefault(cg, set()).add(day)
        return out

def attach_blocks(frame, store):
    result=frame.copy();items=store.blocks()
    result['臨時不可排時段']=[json.dumps([b for b in items if b['cg']==str(cg)],ensure_ascii=False) for cg in result.get('居服員ID',[])]
    return result

def render_leave(store, caregivers, tasks, result, overrides):
    import streamlit as st
    st.caption('整天或多段時段共用同一流程。登錄「請假」後，重疊服務開放代班；其他不可排原因只阻擋派單，固定人選保留。')
    labels={str(r['居服員ID']):str(r['居服員ID']) for _,r in caregivers.iterrows()}
    names=st.session_state.get('_workflow_cg_labels',{})
    if not labels:
        st.info('請先載入居服員資料。');return
    with st.form('availability_form'):
        # 有 key 才能由「載入示範案例」預先帶入；預設值放在 session，避免與元件預設重複設定。
        st.session_state.setdefault('leave_form_day', pd.to_datetime(tasks['日期']).min().date())
        st.session_state.setdefault('leave_form_raw', '09:00-11:00\n14:00-15:00')
        if st.session_state.get('leave_form_cg') not in labels:
            st.session_state.pop('leave_form_cg', None)
        cg=st.selectbox('居服員',list(labels),format_func=lambda x:names.get(x,x),key='leave_form_cg')
        day=st.date_input('日期',value=None,key='leave_form_day') or pd.to_datetime(tasks['日期']).min().date()
        mode=st.radio('不可排範圍',['整天','指定時段（可多段）'],horizontal=True,key='leave_form_mode')
        raw=st.text_area('指定時段：每行一段',help='選整天時忽略此欄。時間格式 HH:MM-HH:MM。',key='leave_form_raw')
        reason=st.selectbox('原因',['請假','看診','教育訓練','家庭事務','其他不可排'],key='leave_form_reason')
        submitted=st.form_submit_button('登錄不可排時間',type='primary')
    if submitted:
        try:
            spans=[('00:00','23:59')] if mode=='整天' else [tuple(line.strip().split('-')) for line in raw.splitlines() if line.strip()]
            if any(len(x)!=2 for x in spans):raise ValueError('請使用 HH:MM-HH:MM，每行一段。')
            store.add_blocks(cg,day.isoformat(),spans,reason)
            assignment=dict(zip(result.get('任務ID',[]),result.get('派單居服員',[])))
            affected=sum(1 for _,t in tasks.iterrows() if str(assignment.get(t['任務ID'],''))==str(cg) and any(b['cg']==str(cg) and overlap(t,b) for b in store.blocks()))
            st.session_state['workflow_notice']=f'已登錄不可排時間，本次有 {affected} 筆已派服務受影響，已標為待安排，請到「02 智慧排班作業」的人工調整改派。'
            if reason=='請假':
                st.session_state['auto_cover_day']=day.isoformat()
                st.session_state['auto_cover_cg']=cg   # 下一次畫面更新時自動找代班
            st.rerun()
        except ValueError as exc:st.error(str(exc))
    blocks=store.blocks()
    if blocks:
        with st.expander(f'已登錄不可排時段 · {len(blocks)}'):
            st.dataframe(pd.DataFrame([{'編號':b['id'],'居服員':names.get(b['cg'],b['cg']),'開始':b['start'],'結束':b['end'],'原因':b['reason']} for b in blocks]),hide_index=True,width='stretch')
            def _block_text(i):
                b=next((x for x in blocks if x['id']==i),None)
                return str(i) if b is None else f"{i}｜{names.get(b['cg'],b['cg'])}｜{b['start'][:10]} {b['start'][11:16]}～{b['end'][11:16]}（{b['reason']}）"
            bid=st.selectbox('撤銷時段', [b['id'] for b in blocks], format_func=_block_text)
            restore=st.checkbox('撤銷後，把這段時間代班的服務還給原居服員', value=True,
                                help='逐筆檢查原居服員是否仍有空；有衝突的保留代班並列出原因。')
            if st.button('撤銷選取時段'):
                chosen=next(b for b in blocks if b['id']==bid)
                store.remove_block(bid)
                if restore:
                    st.session_state['restore_after_revoke']={'cg':chosen['cg'],'start':chosen['start'],'end':chosen['end']}
                    st.session_state['workflow_notice']='已撤銷不可排時段，正在把代班的服務還給原居服員…'
                else:
                    st.session_state['workflow_notice']='已撤銷不可排時段；已改派的服務維持代班。'
                st.rerun()
