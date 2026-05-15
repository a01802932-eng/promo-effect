import os, io, base64, warnings, json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
from flask import Flask, request, jsonify, send_from_directory
import urllib.request

warnings.filterwarnings('ignore')

app = Flask(__name__, static_folder='.')

MARGIN_ASSUMPTION = 0.60
K_WEEKS           = 4

NAVY    = '#0F2A4A'
INK     = '#1E293B'
COL_UNITS = '#2563EB'; COL_REV = '#10B981'; COL_PROF = '#F59E0B'
COL_OFF = '#FCA5A5';   COL_PR  = '#FCD34D'; COL_SPK  = '#C4B5FD'
PRE_C   = '#94A3B8';   DUR_C   = '#2563EB'; POST_C   = '#F59E0B'


def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=155, bbox_inches='tight', facecolor='white')
    buf.seek(0); plt.close(fig)
    return base64.b64encode(buf.read()).decode()


def load_ventas(b):
    df = pd.read_csv(io.BytesIO(b), parse_dates=['tran_date'])
    miss = {'tran_date','dept_nm','prod_nbr','qty','net_sale'} - set(df.columns)
    if miss: raise ValueError(f"Columnas faltantes en ventas: {miss}")
    return df


def load_promos(b):
    prom = pd.read_excel(io.BytesIO(b))
    miss = {'SKU','Fecha_Inicio','Fecha_Fin','Pct_Descuento'} - set(prom.columns)
    if miss: raise ValueError(f"Columnas faltantes en promociones: {miss}")
    prom['SKU_num']      = pd.to_numeric(prom['SKU'], errors='coerce')
    prom['Fecha_Inicio'] = pd.to_datetime(prom['Fecha_Inicio'], dayfirst=True, errors='coerce')
    prom['Fecha_Fin']    = pd.to_datetime(prom['Fecha_Fin'],    dayfirst=True, errors='coerce')
    return prom[['SKU_num','Fecha_Inicio','Fecha_Fin','Pct_Descuento']].dropna(subset=['SKU_num'])


def validate_margin(ven_c):
    vc = ven_c.copy()
    vc['unit_price'] = vc['net_sale'] / vc['qty'].replace(0, np.nan)
    errors = []
    for sku, sub in vc.groupby('prod_nbr'):
        m = sub['unit_price'].mode()
        if m.empty: continue
        reg = m.iloc[0]; est_cost = reg * MARGIN_ASSUMPTION
        bad = sub[sub['unit_price'] < est_cost]
        pct = len(bad)/len(sub) if len(sub) > 0 else 0
        if pct > 0.30:
            errors.append({'sku': int(sku), 'price': round(float(sub['unit_price'].min()),2),
                'cost': round(float(est_cost),2), 'pct': round(pct*100,1),
                'msg': (f"SKU {sku}: el {pct*100:.0f}% de transacciones tienen precio "
                        f"(${sub['unit_price'].min():.2f}) menor al costo estimado (${est_cost:.2f}). "
                        f"Verifica errores de captura, devoluciones mal registradas o precios por debajo de costo.")})
    return errors


def get_name(vc, sku):
    sub = vc[vc['prod_nbr']==sku]
    if 'prod_desc' in sub.columns:
        v = sub['prod_desc'].dropna()
        if not v.empty: return str(v.iloc[0])
    return str(sku)


def build_weekly(sku, ven_c, PROMO_REG):
    s = ven_c[ven_c['prod_nbr']==sku].copy()
    m = s['unit_price'].mode()
    reg = m.iloc[0] if not m.empty else s['unit_price'].median()
    cost = reg * MARGIN_ASSUMPTION
    s['profit'] = s['net_sale'] - s['qty']*cost
    wk = s.groupby('week').agg(units=('qty','sum'),revenue=('net_sale','sum'),
                               profit=('profit','sum'),price=('unit_price','median')).reset_index()
    full = pd.date_range(ven_c['week'].min(), ven_c['week'].max(), freq='W-MON')
    wk = wk.set_index('week').reindex(full).rename_axis('week').reset_index()
    wk[['units','revenue','profit']] = wk[['units','revenue','profit']].fillna(0)
    wk['price'] = wk['price'].fillna(reg)
    wk['price_drop_pct'] = (reg - wk['price']) / reg * 100
    wk['promo_official'] = False
    for _, p in PROMO_REG[PROMO_REG['SKU_num']==sku].iterrows():
        if pd.notna(p['Fecha_Inicio']) and pd.notna(p['Fecha_Fin']):
            wk.loc[(wk['week']>=p['Fecha_Inicio']-pd.Timedelta(days=7))&(wk['week']<=p['Fecha_Fin']),'promo_official']=True
    wk['promo_price20'] = wk['price_drop_pct']>=20
    p90 = wk['units'].quantile(0.90)
    wk['promo_spike'] = wk['units']>=max(p90,20)
    wk['promo'] = wk['promo_official']|wk['promo_price20']
    if not wk['promo'].any(): wk['promo'] = wk['promo_spike']
    wk['regular_pr']=reg; wk['unit_cost']=cost
    return wk


def identify_windows(w):
    wins=[]; in_p=False; start=None
    for i in range(len(w)):
        if w.iloc[i]['promo'] and not in_p: start=i; in_p=True
        elif not w.iloc[i]['promo'] and in_p: wins.append((start,i-1)); in_p=False
    if in_p: wins.append((start,len(w)-1))
    return wins


def event_study(w, k=K_WEEKS):
    wins=identify_windows(w); rows=[]
    for (s,e) in wins:
        pi=list(range(max(0,s-k),s)); di=list(range(s,e+1)); po=list(range(e+1,min(len(w),e+1+k)))
        rows.append({'pre_u':w.iloc[pi]['units'].mean() if pi else np.nan,'dur_u':w.iloc[di]['units'].mean(),
            'post_u':w.iloc[po]['units'].mean() if po else np.nan,
            'pre_r':w.iloc[pi]['revenue'].mean() if pi else np.nan,'dur_r':w.iloc[di]['revenue'].mean(),
            'post_r':w.iloc[po]['revenue'].mean() if po else np.nan,
            'pre_p':w.iloc[pi]['profit'].mean() if pi else np.nan,'dur_p':w.iloc[di]['profit'].mean(),
            'post_p':w.iloc[po]['profit'].mean() if po else np.nan})
    return pd.DataFrame(rows)


def safe_pct(d,p):
    return round((d/p-1)*100,1) if (pd.notna(p) and p>0) else None

def fmt_pct(v): return f"{v:+.1f}%" if v is not None else '—'

def auto_verdict(du,pu,dg,pg):
    if du is None: return "Datos insuficientes"
    if du>500: return "Spike puntual sin efecto duradero" if (pu or 0)<5 else "Spike con efecto residual"
    if (pg or 0)>30: return "Demanda activada que se sostiene"
    if (pg or 0)>0 and (dg or 0)<0: return "Efecto residual positivo post-promo"
    if (dg or 0)<-20: return "Destruye margen — evaluar ROI neto"
    return "Efecto mixto — revisar por tienda"


def make_timeline(skus, weekly, names):
    n=len(skus)
    fig,axes=plt.subplots(n,3,figsize=(16,3.5*n),sharex=True)
    if n==1: axes=axes[np.newaxis,:]
    metrics=[('units','Unidades/semana',COL_UNITS),('revenue','Ingresos/semana ($)',COL_REV),('profit','Ganancia/semana ($)',COL_PROF)]
    for i,sku in enumerate(skus):
        w=weekly[sku]
        for j,(col,lbl,c) in enumerate(metrics):
            ax=axes[i,j]
            for _,r in w.iterrows():
                if r['promo_official']: ax.axvspan(r['week'],r['week']+pd.Timedelta(days=7),color=COL_OFF,alpha=.5,lw=0)
                elif r['promo_price20']: ax.axvspan(r['week'],r['week']+pd.Timedelta(days=7),color=COL_PR,alpha=.5,lw=0)
                elif (not w['promo_price20'].any() and not w['promo_official'].any() and r['promo_spike']):
                    ax.axvspan(r['week'],r['week']+pd.Timedelta(days=7),color=COL_SPK,alpha=.5,lw=0)
            ax.plot(w['week'],w[col],color=c,lw=1.5)
            ax.fill_between(w['week'],0,w[col],color=c,alpha=.15)
            if j==0: ax.set_ylabel(f'SKU {sku}\n{str(names[sku])[:16]}',fontsize=7.5,fontweight='bold')
            if i==0: ax.set_title(lbl,fontsize=10,fontweight='bold',pad=8)
            ax.tick_params(labelsize=7.5)
            ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1,4,7,10]))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b\n%Y'))
            ax.grid(axis='y',ls=':',alpha=.4)
            ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    fig.legend(handles=[Patch(facecolor=COL_OFF,alpha=.5,label='Promo oficial'),
        Patch(facecolor=COL_PR,alpha=.5,label='Proxy: descuento ≥20%'),
        Patch(facecolor=COL_SPK,alpha=.5,label='Proxy: spike unidades p90')],
        loc='lower center',ncol=3,fontsize=9,frameon=False,bbox_to_anchor=(0.5,0.0))
    fig.suptitle('Línea de tiempo semanal — Unidades, Ingresos y Ganancia',fontsize=12,fontweight='bold',y=0.998)
    plt.tight_layout(rect=[0,0.04,1,0.97])
    return fig_to_b64(fig)


def make_event_study(skus, weekly, names):
    n=len(skus); cols=min(n,3); rows_n=(n+cols-1)//cols
    fig,axes=plt.subplots(rows_n,cols,figsize=(6*cols,4.5*rows_n))
    af=np.array(axes).flatten() if n>1 else [axes]
    for idx,sku in enumerate(skus):
        ev=event_study(weekly[sku]); ax=af[idx]
        if ev.empty: ax.set_title(f'SKU {sku}\nSin ventanas de promo',fontsize=9); ax.axis('off'); continue
        e=ev.mean(numeric_only=True)
        iv=lambda d,p:(d/p*100) if (pd.notna(p) and p>0) else 100
        dur=[iv(e['dur_u'],e['pre_u']),iv(e['dur_r'],e['pre_r']),iv(e['dur_p'],e['pre_p'])]
        post=[iv(e['post_u'],e['pre_u']),iv(e['post_r'],e['pre_r']),iv(e['post_p'],e['pre_p'])]
        x=np.arange(3); bw=0.27
        b1=ax.bar(x-bw,[100,100,100],bw,label='Pre=100',color=PRE_C)
        b2=ax.bar(x,dur,bw,label='Durante',color=DUR_C)
        b3=ax.bar(x+bw,post,bw,label='Post',color=POST_C)
        ax.axhline(100,color='#475569',lw=0.7,ls='--',alpha=0.6)
        ym=max(dur+post+[120])
        for bars,vals in [(b1,[100,100,100]),(b2,dur),(b3,post)]:
            for b,v in zip(bars,vals):
                ax.text(b.get_x()+b.get_width()/2,v+ym*0.012,f'{v:,.0f}',ha='center',va='bottom',fontsize=8,color=INK,fontweight='bold' if v!=100 else 'normal')
        ax.set_xticks(x); ax.set_xticklabels(['Unidades','Ingresos','Ganancia'],fontsize=9)
        ax.set_title(f'SKU {sku} · {str(names[sku])[:16]}',fontsize=9.5,fontweight='bold',color=NAVY,pad=6)
        ax.set_ylabel('Índice (Pre=100)',fontsize=8)
        ax.grid(axis='y',ls=':',alpha=0.4); ax.tick_params(labelsize=8)
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        if idx==0: ax.legend(fontsize=8,frameon=False,loc='upper right')
    for idx in range(len(skus),len(af)): af[idx].axis('off')
    fig.suptitle('Event Study — Pre / Durante / Post  (±4 sem, Pre=100)',fontsize=11,fontweight='bold',y=1.01)
    plt.tight_layout()
    return fig_to_b64(fig)


def llm_conclusion(dept, skus, names, conclusions, comparativo):
    api_key = os.environ.get('ANTHROPIC_API_KEY','')
    if not api_key: return None
    rows = '\n'.join(f"- SKU {c['sku']} ({c['name']}): unid.dur={c['dur_u']}, post={c['post_u']}; gan.dur={c['dur_g']}, post={c['post_g']}; veredicto='{c['veredicto']}'" for c in conclusions)
    comp = '\n'.join(f"- SKU {r['sku']} ({r['nombre']}): Δunid={r['delta_u']}%, Δing={r['delta_r']}%, Δgan={r['delta_g']}%" for r in comparativo) or '(sin comparativo suficiente)'
    prompt = f"""Eres analista de retail experto en datos de OfficeMax México.
Se analizaron {len(skus)} SKUs del departamento "{dept}".

Event Study (Pre=100, ±4 semanas):
{rows}

Comparativo semanal Promo vs No-Promo:
{comp}

Escribe UNA conclusión ejecutiva en español, máximo 5 oraciones, cubriendo:
1. Efecto general de las promos en el departamento
2. SKUs que mejor y peor responden
3. Recomendación concreta de timing o tipo de promo
4. Advertencia de riesgo de margen si aplica

Tono: profesional, directo, sin bullets. Solo párrafo continuo."""
    payload = json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":400,"messages":[{"role":"user","content":prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages",data=payload,
        headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=25) as r:
            return json.loads(r.read())['content'][0]['text']
    except: return None


@app.route('/')
def index(): return send_from_directory('.','index.html')

@app.route('/logo-officemax.png')
def logo(): return send_from_directory('.','logo-officemax.png')


@app.route('/departments', methods=['POST'])
def departments():
    if 'ventas' not in request.files: return jsonify({'error':'Falta archivo ventas'}),400
    try:
        df=load_ventas(request.files['ventas'].read())
        return jsonify({'departments': sorted(df['dept_nm'].dropna().unique().tolist())})
    except ValueError as e: return jsonify({'error':str(e)}),400


@app.route('/skus', methods=['POST'])
def skus_for_dept():
    if 'ventas' not in request.files: return jsonify({'error':'Falta archivo ventas'}),400
    dept=request.form.get('dept','').strip()
    try: df=load_ventas(request.files['ventas'].read())
    except ValueError as e: return jsonify({'error':str(e)}),400
    vc=df[df['dept_nm'].str.upper()==dept.upper()].copy() if dept else df
    if vc.empty: return jsonify({'error':f'Departamento "{dept}" no encontrado'}),400
    vc['unit_price']=vc['net_sale']/vc['qty'].replace(0,np.nan)
    g=vc.groupby('prod_nbr').agg(units=('qty','sum'),revenue=('net_sale','sum')).reset_index()
    g['name']=g['prod_nbr'].apply(lambda s: get_name(vc,s))
    g=g.sort_values('units',ascending=False)
    return jsonify({'skus':[{'prod_nbr':int(r['prod_nbr']),'name':r['name'],'units':int(r['units']),'revenue':round(float(r['revenue']),2)} for _,r in g.iterrows()]})


@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        if 'ventas' not in request.files: return jsonify({'error':'Falta el archivo de ventas.'}),400
        try: ven=load_ventas(request.files['ventas'].read())
        except ValueError as e: return jsonify({'error':str(e)}),400

        dept=request.form.get('dept','').strip().upper()
        if dept:
            ven_c=ven[ven['dept_nm'].str.upper()==dept].copy()
            if ven_c.empty:
                av=ven['dept_nm'].unique().tolist()[:20]
                return jsonify({'error':f'Departamento "{dept}" no encontrado. Disponibles: {av}'}),400
        else:
            dept=ven['dept_nm'].value_counts().idxmax(); ven_c=ven[ven['dept_nm']==dept].copy()

        ven_c['unit_price']=ven_c['net_sale']/ven_c['qty'].replace(0,np.nan)
        merr=validate_margin(ven_c)
        if merr: return jsonify({'margin_errors':merr}),422

        sel_raw=request.form.get('skus','')
        if sel_raw:
            sel=[int(x.strip()) for x in sel_raw.split(',') if x.strip().isdigit()]
            sel=[s for s in sel if s in ven_c['prod_nbr'].values]
            if not sel: return jsonify({'error':'Ningún SKU seleccionado existe en el departamento.'}),400
        else:
            g=ven_c.groupby('prod_nbr').agg(units=('qty','sum'),revenue=('net_sale','sum'),txns=('tran_date','count'))
            for col in ['units','revenue','txns']: g[f'r_{col}']=g[col].rank(ascending=False)
            g['score']=g[['r_units','r_revenue','r_txns']].mean(axis=1)
            sel=list(g.nsmallest(3,'score').index)

        names={sku:get_name(ven_c,sku) for sku in sel}

        if 'promos' in request.files and request.files['promos'].filename!='':
            try: PROMO_REG=load_promos(request.files['promos'].read())
            except ValueError as e: return jsonify({'error':str(e)}),400
        else:
            PROMO_REG=pd.DataFrame(columns=['SKU_num','Fecha_Inicio','Fecha_Fin','Pct_Descuento'])

        ven_c['week']=ven_c['tran_date'].dt.to_period('W-SUN').dt.start_time
        weekly={sku:build_weekly(sku,ven_c,PROMO_REG) for sku in sel}

        img_tl=make_timeline(sel,weekly,names)
        img_ev=make_event_study(sel,weekly,names)

        conclusions=[]
        for sku in sel:
            ev=event_study(weekly[sku])
            if ev.empty:
                conclusions.append({'sku':int(sku),'name':names[sku],'veredicto':'Sin ventanas de promo','dur_u':'—','post_u':'—','dur_r':'—','post_r':'—','dur_g':'—','post_g':'—'}); continue
            e=ev.mean(numeric_only=True)
            du=safe_pct(e['dur_u'],e['pre_u']); pu=safe_pct(e['post_u'],e['pre_u'])
            dr=safe_pct(e['dur_r'],e['pre_r']); pr=safe_pct(e['post_r'],e['pre_r'])
            dg=safe_pct(e['dur_p'],e['pre_p']); pg=safe_pct(e['post_p'],e['pre_p'])
            conclusions.append({'sku':int(sku),'name':names[sku],
                'dur_u':fmt_pct(du),'post_u':fmt_pct(pu),'dur_r':fmt_pct(dr),'post_r':fmt_pct(pr),
                'dur_g':fmt_pct(dg),'post_g':fmt_pct(pg),'veredicto':auto_verdict(du,pu,dg,pg)})

        comparativo=[]
        for sku in sel:
            w=weekly[sku]
            if w['promo'].any() and (~w['promo']).any():
                g2=w.groupby('promo').agg(units=('units','mean'),revenue=('revenue','mean'),profit=('profit','mean'))
                if True in g2.index and False in g2.index:
                    on,off=g2.loc[True],g2.loc[False]
                    if off['units']>0 and off['revenue']>0 and off['profit']!=0:
                        comparativo.append({'sku':int(sku),'nombre':names[sku],
                            'sem_promo':int(w['promo'].sum()),'sem_no_promo':int((~w['promo']).sum()),
                            'delta_u':round((on['units']/off['units']-1)*100,1),
                            'delta_r':round((on['revenue']/off['revenue']-1)*100,1),
                            'delta_g':round((on['profit']/off['profit']-1)*100,1)})

        ai=llm_conclusion(dept,sel,names,conclusions,comparativo)

        return jsonify({'dept':dept,'skus':[int(s) for s in sel],'names':{str(k):v for k,v in names.items()},
            'timeline':img_tl,'event':img_ev,'conclusions':conclusions,'comparativo':comparativo,'ai_conclusion':ai})
    except Exception as e:
        import traceback
        return jsonify({'error':str(e),'trace':traceback.format_exc()}),500


if __name__=='__main__':
    port=int(os.environ.get('PORT',5000))
    app.run(host='0.0.0.0',port=port,debug=False)
