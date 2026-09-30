import os,re,json,time,sqlite3,unicodedata
from pathlib import Path
from datetime import datetime,timedelta,timezone
from typing import Any,Optional
import requests,cloudinary,cloudinary.uploader
from dotenv import load_dotenv
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse,JSONResponse,Response

BASE_DIR=Path(__file__).resolve().parent; load_dotenv(BASE_DIR/'.env')

DEFAULT_STORE_ID=os.getenv('DEFAULT_STORE_ID','trendyol_ayse').strip() or 'trendyol_ayse'
FIXED_BRAND='Ayşe Tesettür'; FIXED_ORIGIN='Türkiye'; FIXED_FABRIC='Dokuma'; FIXED_GENDER='Kadın / Kız'
CLOUD_NAME=os.getenv('CLOUDINARY_CLOUD_NAME','').strip(); CLOUD_KEY=os.getenv('CLOUDINARY_API_KEY','').strip(); CLOUD_SECRET=os.getenv('CLOUDINARY_API_SECRET','').strip()
DB=BASE_DIR/'store.db'
app=FastAPI(title='Ayşe Tesettür - Trendyol Management System V5',version='5.2.0')

def _env(name, default=''):
    return os.getenv(name, default).strip()

def _profile(prefix, fallback_prefix=None, name=''):
    fb=fallback_prefix or prefix
    return {
        'id':_env(prefix+'_STORE_ID',_env(fb+'_STORE_ID','')),
        'name':_env(prefix+'_STORE_NAME',name),
        'platform':_env(prefix+'_PLATFORM','trendyol').lower(),
        'seller_id':_env(prefix+'_SELLER_ID',_env(fb+'_SELLER_ID','')),
        'storefront':_env(prefix+'_STOREFRONT',_env(fb+'_STOREFRONT','TR') or 'TR'),
        'user_agent':_env(prefix+'_USER_AGENT',_env(fb+'_USER_AGENT','Ayse Tesettur Trendyol Management System V5')),
        'api_key':_env(prefix+'_API_KEY',_env(fb+'_API_KEY','')),
        'api_secret':_env(prefix+'_API_SECRET',_env(fb+'_API_SECRET','')),
        'brand_id':_env(prefix+'_BRAND_ID',_env(fb+'_BRAND_ID','')),
        'brand':_env(prefix+'_BRAND',FIXED_BRAND),
    }

STORE_PROFILES={}
_p1=_profile('TY1','TRENDYOL','Trendyol Ayşe Tesettür')
_p1['id']=_p1['id'] or 'trendyol_ayse'; _p1['name']=_p1['name'] or 'Trendyol Ayşe Tesettür'; _p1['platform']='trendyol'; STORE_PROFILES[_p1['id']]=_p1
_p2=_profile('TY2',None,'Trendyol Aynur Tesettür')
_p2['id']=_p2['id'] or 'trendyol_store2'; _p2['name']=_p2['name'] or 'Trendyol Aynur Tesettür'; _p2['platform']='trendyol'
if _p2['seller_id'] or _p2['api_key'] or _p2['api_secret']: STORE_PROFILES[_p2['id']]=_p2
_hb=_profile('HB1',None,'Hepsiburada aynur tesettur1'); _hb['id']=_hb['id'] or 'hepsiburada'; _hb['name']=_hb['name'] or 'Hepsiburada aynur tesettur1'; _hb['platform']='hepsiburada'
if _hb['seller_id'] or _hb['api_key'] or _hb['api_secret']: STORE_PROFILES[_hb['id']]=_hb

def store_or_default(store_id=None):
    sid=(store_id or DEFAULT_STORE_ID).strip()
    if sid not in STORE_PROFILES:
        sid=next(iter(STORE_PROFILES),DEFAULT_STORE_ID)
    return STORE_PROFILES.get(sid)

def store_label(store_id=None):
    p=store_or_default(store_id); return p['name'] if p else str(store_id or DEFAULT_STORE_ID)

def norm(v):
    s='' if v is None else str(v).strip().lower(); s=s.translate(str.maketrans({'ı':'i','ş':'s','ğ':'g','ü':'u','ö':'o','ç':'c'})); s=unicodedata.normalize('NFKD',s); return re.sub(r'\s+',' ',' '.join(c for c in s if not unicodedata.combining(c))).strip()
def si(v):
    try:return int(v)
    except:return None
def sf(v):
    try:return float(str(v).replace(',','.'))
    except:return None
def nowms():return int(datetime.now(timezone.utc).timestamp()*1000)
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def initdb():
    c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,store_id TEXT DEFAULT 'trendyol_ayse',content_id TEXT UNIQUE,product_main_id TEXT,title TEXT,category_id INTEGER,category_name TEXT,brand_id INTEGER,description TEXT,image_url TEXT,status TEXT,synced_at INTEGER);
CREATE TABLE IF NOT EXISTS variants(id INTEGER PRIMARY KEY,store_id TEXT DEFAULT 'trendyol_ayse',content_id TEXT,variant_id TEXT,barcode TEXT UNIQUE,stock_code TEXT,product_main_id TEXT,quantity REAL,sale_price REAL,list_price REAL,status TEXT,stock_modified_at INTEGER,synced_at INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,store_id TEXT DEFAULT 'trendyol_ayse',shipment_package_id TEXT UNIQUE,order_number TEXT,status TEXT,cargo_provider TEXT,order_date INTEGER,last_modified_date INTEGER,total_price REAL,raw_json TEXT,synced_at INTEGER);
CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY,store_id TEXT DEFAULT 'trendyol_ayse',shipment_package_id TEXT,order_line_id TEXT,barcode TEXT,product_name TEXT,quantity REAL,price REAL,status TEXT,raw_json TEXT,synced_at INTEGER,UNIQUE(shipment_package_id,order_line_id));
CREATE TABLE IF NOT EXISTS settlements(id INTEGER PRIMARY KEY,store_id TEXT DEFAULT 'trendyol_ayse',transaction_id TEXT UNIQUE,transaction_type TEXT,order_number TEXT,barcode TEXT,transaction_date INTEGER,seller_revenue REAL,commission REAL,amount REAL,raw_json TEXT,synced_at INTEGER);
CREATE TABLE IF NOT EXISTS sync_log(id INTEGER PRIMARY KEY,store_id TEXT DEFAULT 'trendyol_ayse',sync_type TEXT,finished_at INTEGER,status TEXT,message TEXT);''')
    for table in ('products','variants','orders','order_items','settlements','sync_log'):
        cols=[r['name'] for r in c.execute(f'PRAGMA table_info({table})').fetchall()]
        if 'store_id' not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN store_id TEXT DEFAULT 'trendyol_ayse'")
    c.commit(); c.close()
initdb()

def headers(store_id=None,json_body=False):
    p=store_or_default(store_id)
    if not p: raise HTTPException(400,'Mağaza bulunamadı.')
    if p['platform']!='trendyol': raise HTTPException(400,'Bu sürümde ürün/sipariş API senkronizasyonu Trendyol mağazaları için aktiftir.')
    if not p['api_key'] or not p['api_secret']: raise HTTPException(400,f"{p['name']} için API Key / API Secret .env dosyasında eksik.")
    h={'Authorization':requests.auth._basic_auth_str(p['api_key'],p['api_secret']),'User-Agent':p['user_agent'],'Accept':'application/json','storeFrontCode':p['storefront'],'Accept-Language':'tr'}
    if json_body:h['Content-Type']='application/json'
    return h
def url(service,path):
    return {'product':'https://apigw.trendyol.com/integration/product/','inventory':'https://apigw.trendyol.com/integration/inventory/','order':'https://apigw.trendyol.com/integration/order/','finance':'https://apigw.trendyol.com/integration/finance/'}[service]+path.lstrip('/')
def get(service,path,params=None,timeout=90,store_id=None):
    try:r=requests.get(url(service,path),headers=headers(store_id),params=params,timeout=timeout)
    except requests.RequestException as e:raise HTTPException(502,f'Trendyol bağlantı hatası: {e}')
    if not r.ok:raise HTTPException(r.status_code,f'Trendyol GET hatası: {r.text[:2500]}')
    try:return r.json()
    except:return {}
def post(service,path,payload,timeout=120,store_id=None):
    try:r=requests.post(url(service,path),headers=headers(store_id,True),json=payload,timeout=timeout)
    except requests.RequestException as e:raise HTTPException(502,f'Trendyol bağlantı hatası: {e}')
    if not r.ok:raise HTTPException(r.status_code,f'Trendyol POST hatası: {r.text[:3000]}')
    try:return r.json()
    except:return {'raw':r.text}
def put(service,path,payload,timeout=120,store_id=None):
    try:r=requests.put(url(service,path),headers=headers(store_id,True),json=payload,timeout=timeout)
    except requests.RequestException as e:raise HTTPException(502,f'Trendyol bağlantı hatası: {e}')
    if not r.ok:raise HTTPException(r.status_code,f'Trendyol PUT hatası: {r.text[:3000]}')
    try:return r.json()
    except:return {'raw':r.text}

def catroots(d):
    if isinstance(d,list):return d
    if isinstance(d,dict):
        for k in ('categories','content','items','data'):
            if isinstance(d.get(k),list):return d[k]
    return []
def kids(x):
    for k in ('subCategories','children','subcategories'):
        if isinstance(x.get(k),list):return x[k]
    return []
def cname(x):return str(x.get('name') or x.get('categoryName') or x.get('displayName') or '').strip()
def cid(x):return si(x.get('id') or x.get('categoryId'))
def walk(nodes,path=None):
    path=path or []
    for x in nodes:
        if not isinstance(x,dict):continue
        p=path+[cname(x)] if cname(x) else path; ch=kids(x); yield x,p,not ch; yield from walk(ch,p)
def catsearch(q,store_id=None):
    d=get('product','product-categories',{'name':q},store_id=store_id); out=[]; nq=norm(q)
    for x,p,leaf in walk(catroots(d)):
        i=cid(x); n=cname(x)
        if i and n and (not nq or nq in norm(n) or nq in norm(' > '.join(p))):out.append({'id':i,'name':n,'leaf':leaf,'pathText':' > '.join(p)})
    u={x['id']:x for x in out}; return sorted(u.values(),key=lambda x:(not x['leaf'],norm(x['pathText'])))[:100]
def findcat(i,store_id=None):
    for x,p,leaf in walk(catroots(get('product','product-categories',store_id=store_id))):
        if cid(x)==int(i):return {'id':int(i),'name':cname(x),'leaf':leaf,'pathText':' > '.join(p)}
    return None

def aid(a):return a.get('attributeId') or a.get('id') or (a.get('attribute') or {}).get('id')
def aname(a):return a.get('attributeName') or a.get('name') or (a.get('attribute') or {}).get('name') or ''
def vals(a):return a.get('attributeValues') or a.get('values') or []
def vid(v):return v.get('attributeValueId') or v.get('id') or v.get('valueId')
def vname(v):return v.get('attributeValue') or v.get('name') or v.get('value') or v.get('displayName') or ''
def custom(a):return any(a.get(k) is True for k in ('allowCustom','customValues','isCustomValue','allowCustomValue','isCustom'))
def attrs(d):
    raw=d if isinstance(d,list) else (d.get('categoryAttributes') or d.get('attributes') or d.get('content') or d.get('items') or d.get('data') or []) if isinstance(d,dict) else []
    out=[]
    for x in raw:
        if not isinstance(x,dict):continue
        a=x.get('attribute') if isinstance(x.get('attribute'),dict) else {}
        out.append({'attributeId':si(a.get('id') or x.get('attributeId') or x.get('id')),'attributeName':str(a.get('name') or x.get('attributeName') or x.get('name') or ''),'required':bool(x.get('required')),'allowCustom':bool(x.get('allowCustom')),'attributeValues':x.get('attributeValues') if isinstance(x.get('attributeValues'),list) else []})
    return out
def getattrs(i,store_id=None):
    aa=attrs(get('product',f'categories/{i}/attributes',store_id=store_id))
    for a in aa:
        if vals(a):continue
        z=si(aid(a))
        if not z:continue
        try:
            d=get('product',f'categories/{i}/attributes/{z}/values',{'page':0,'size':1000},store_id=store_id); a['attributeValues']=d if isinstance(d,list) else d.get('content') or d.get('attributeValues') or d.get('values') or d.get('items') or []
        except:pass
    return aa
def findattr(aa,names):
    w={norm(x) for x in names}
    return next((a for a in aa if norm(aname(a)) in w),None)
def findval(a,name):
    if not a:return None
    return next((v for v in vals(a) if norm(vname(v))==norm(name)),None)
def brandid(store_id=None):
    p=store_or_default(store_id)
    if p and si(p.get('brand_id')):return si(p['brand_id'])
    brand=(p.get('brand') if p else FIXED_BRAND) or FIXED_BRAND
    try:
        d=get('product','brands/by-name',{'name':brand},store_id=store_id); arr=d if isinstance(d,list) else d.get('content') or d.get('brands') or d.get('items') or d.get('data') or [d]
        for b in arr:
            if isinstance(b,dict) and norm(b.get('name') or b.get('brandName'))==norm(brand) and si(b.get('id') or b.get('brandId')):return si(b.get('id') or b.get('brandId'))
    except:pass
    return None

def buildattrs(aa,color,src,val,size):
    out=[]; problems=[]
    def fixed(names,text,label):
        a=findattr(aa,names)
        if not a:return
        i=si(aid(a)); vv=None
        for n in (['TR','Türkiye','Turkey'] if label=='Menşei' else [text]):
            vv=findval(a,n)
            if vv:break
        if i and vv and vid(vv)!=None:out.append({'attributeId':i,'attributeValueId':si(vid(vv))})
        elif custom(a) and label!='Menşei':out.append({'attributeId':i,'customAttributeValue':text})
        elif a.get('required') and label!='Menşei':problems.append(f"{label} için '{text}' Trendyol canlı değerlerinde bulunamadı.")
    fixed(['Marka','Brand'],FIXED_BRAND,'Marka'); fixed(['Menşei','Mensei','Origin'],FIXED_ORIGIN,'Menşei'); fixed(['Kumaş Tipi','Kumas Tipi','Fabric Type'],FIXED_FABRIC,'Kumaş Tipi'); fixed(['Cinsiyet','Gender'],FIXED_GENDER,'Cinsiyet')
    a=findattr(aa,['Renk','Color']); i=si(aid(a)) if a else None; vv=findval(a,color) if a else None
    if i and vv and vid(vv)!=None:out.append({'attributeId':i,'attributeValueId':si(vid(vv))})
    elif a and custom(a):out.append({'attributeId':i,'customAttributeValue':color})
    elif a and a.get('required'):problems.append(f'Renk değeri kabul edilmiyor: {color}')
    a=findattr(aa,['Web Renk','Web Color','Web Renkleri','Web Colors']); i=si(aid(a)) if a else None
    if i and src and val:out.append({'attributeId':i,'attributeValueId':si(val)})
    elif a and a.get('required'):problems.append('Web Renk için canlı Trendyol renk IDsi bulunamadı.')
    a=findattr(aa,['Beden','Size']); i=si(aid(a)) if a else None; vv=findval(a,size) if a else None
    if i and vv and vid(vv)!=None:out.append({'attributeId':i,'attributeValueId':si(vid(vv))})
    elif i and custom(a):out.append({'attributeId':i,'customAttributeValue':size})
    elif a and a.get('required'):problems.append(f'Beden değeri bulunamadı: {size}')
    supplied={x['attributeId'] for x in out}; ignored={norm(x) for x in ['Marka','Menşei','Mensei','Kumaş Tipi','Kumas Tipi','Cinsiyet','Renk','Color','Web Renk','Web Color','Beden','Size']}
    for a in aa:
        i=si(aid(a)); n=norm(aname(a))
        if i and a.get('required') and i not in supplied and n not in ignored:problems.append(f'Zorunlu kategori özelliği eksik: {aname(a)}')
    return out,list(dict.fromkeys(problems))

def cloud(file):
    if not CLOUD_NAME or not CLOUD_KEY or not CLOUD_SECRET:raise HTTPException(400,'Cloudinary bilgileri .env dosyasında eksik.')
    cloudinary.config(cloud_name=CLOUD_NAME,api_key=CLOUD_KEY,api_secret=CLOUD_SECRET,secure=True); file.file.seek(0)
    try:r=cloudinary.uploader.upload(file.file,folder='ayse_tesettur/trendyol',resource_type='image'); return r['secure_url']
    except Exception as e:raise HTTPException(500,f'Cloudinary yükleme hatası: {e}')
    # ============================================================
# BASIC ENDPOINTS
# ============================================================
@app.get('/health')
def health():return {'ok':True,'version':'5.2.0','database':str(DB),'stores':[{k:v['name']} for k,v in STORE_PROFILES.items()]}

@app.get('/stores')
def stores():
    return {'stores':[{'id':k,'name':v['name'],'platform':v['platform'],'configured':bool(v.get('api_key') and v.get('api_secret'))} for k,v in STORE_PROFILES.items()]}

@app.get('/category-search')
def category_search(q:str='',store_id:str=''):
    if len(q.strip())<2:return {'categories':[],'message':'En az 2 harf yaz.'}
    return {'categories':catsearch(q.strip(),store_id)}

@app.get('/category-select/{i}')
def category_select(i:int,store_id:str=''):
    c=findcat(i,store_id)
    if not c:raise HTTPException(404,'Kategori bulunamadı.')
    if not c['leaf']:return {'ok':False,'category':c,'message':'Bu kategori alt kategori içeriyor. En alt kategoriye tıklamalısın.'}
    aa=getattrs(i,store_id); ca=[a for a in aa if norm(aname(a)) in {norm('Web Renk'),norm('Web Color'),norm('Renk'),norm('Color')}]; web=findattr(ca,['Web Renk','Web Color']); renk=findattr(ca,['Renk','Color']); src=web if web and vals(web) else renk
    colors=[]; seen=set()
    if src:
        for v in vals(src):
            z=si(vid(v)); n=str(vname(v)).strip()
            if z and n and z not in seen:seen.add(z);colors.append({'id':z,'name':n})
    return {'ok':True,'category':c,'colorSource':{'attributeId':si(aid(src)) if src else None,'attributeName':aname(src) if src else None},'colors':colors}

# ============================================================
# PREPARE — ürün yükleme
# ============================================================
@app.post('/prepare')
async def prepare(request:Request):
    f=await request.form()
    store_id=str(f.get('store_id') or DEFAULT_STORE_ID).strip(); p=store_or_default(store_id)
    if not p:raise HTTPException(400,'Mağaza bulunamadı.')
    if p['platform']!='trendyol':raise HTTPException(400,'Bu V5 sürümünde ürün yükleme şu anda Trendyol mağazaları için aktiftir. Hepsiburada bağlantısı sonraki entegrasyondur.')
    if not p.get('seller_id'):raise HTTPException(400,f"{p['name']} için Seller ID .env dosyasında eksik.")
    c_id=si(f.get('category_id')); model=str(f.get('model_code') or '').strip(); title=str(f.get('title') or '').strip(); price=sf(f.get('price')); stock=si(f.get('stock')); vat=si(f.get('vat_rate')) or 20; sizes=[x.strip() for x in str(f.get('sizes') or '').split(',') if x.strip()]; actual=str(f.get('actual_fabric') or '').strip(); desc=str(f.get('description') or '').strip(); n=si(f.get('color_count')) or 0
    if not c_id or not model or not title:raise HTTPException(400,'Kategori, model kodu ve başlık zorunlu.')
    if price is None or price<=0 or stock is None or stock<0 or not sizes or not 1<=n<=10:raise HTTPException(400,'Fiyat, stok, beden ve renk bilgilerini kontrol et.')
    c=findcat(c_id,store_id)
    if not c or not c['leaf']:raise HTTPException(400,'Seçilen kategori leaf değil veya bulunamadı.')
    aa=getattrs(c_id,store_id); bid=brandid(store_id)
    if not aa:raise HTTPException(400,'Kategori özellikleri alınamadı.')
    if not bid:raise HTTPException(400,"Ayşe Tesettür marka ID'si bulunamadı.")
    colors=[]; used=set()
    for i in range(1,n+1):
        name=str(f.get(f'color_{i}_name') or '').strip(); src=si(f.get(f'color_{i}_source_id')); val=si(f.get(f'color_{i}_value_id')); files=[x for x in f.getlist(f'color_{i}_images') if getattr(x,'filename','')]
        if not name or not src or not val or not 1<=len(files)<=5:raise HTTPException(400,f'Renk {i} bilgilerini kontrol et.')
        if val in used: raise HTTPException(400,f'Renk {i} daha önce seçilmiş.')
        used.add(val)
        colors.append((name,src,val,files))
    if len(colors)*len(sizes)>1000:raise HTTPException(400,'Toplam varyant 1000i geçemez.')
    items=[]; summary=[]
    for name,src,val,files in colors:
        urls=[cloud(x) for x in files]; summary.append({'name':name,'imageCount':len(urls)})
        for size in sizes:
            at,probs=buildattrs(aa,name,src,val,size)
            if probs:raise HTTPException(400,'Ürün gönderilemedi: '+' | '.join(probs[:20]))
            bc=f"{model}-{norm(name).replace(' ','-')}-{size}".upper()
            items.append({'barcode':bc,'title':title,'description':desc or f'{title}. Kumaş: {actual or "Şifon"}.','productMainId':model,'brandId':bid,'categoryId':c_id,'quantity':stock,'stockCode':bc,'origin':'TR','dimensionalWeight':1,'listPrice':price,'salePrice':price,'vatRate':vat,'images':[{'url':u} for u in urls],'attributes':at})
    result=post('product',f"sellers/{p['seller_id']}/v2/products",{'items':items},store_id=store_id); batch=result.get('batchRequestId') if isinstance(result,dict) else None; batch_result=None
    if batch:
        for _ in range(5):
            try:
                batch_result=get('product',f"sellers/{p['seller_id']}/products/batch-requests/{batch}",store_id=store_id); st=str(batch_result.get('status','')).upper()
                if st in ('COMPLETED','FAILED'):break
            except:break
            time.sleep(1.5)
    con=db(); t=nowms()
    for x in items:con.execute('INSERT OR REPLACE INTO variants(store_id,content_id,variant_id,barcode,stock_code,product_main_id,quantity,sale_price,list_price,status,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(store_id,'', '',f'{store_id}::{x["barcode"]}',x['stockCode'],x['productMainId'],x['quantity'],x['salePrice'],x['listPrice'],'upload_submitted',t))
    con.commit();con.close()
    return {'ok':True,'storeId':store_id,'storeName':p['name'],'model':model,'colorCount':len(colors),'totalImageCount':sum(x['imageCount'] for x in summary),'variantCount':len(items),'batchRequestId':batch,'trendyol':result,'batchResult':batch_result,'colors':summary,'message':'Ürün Trendyol V2ye gönderildi ve yerel geçmişe kaydedildi.'}

# ============================================================
# PAGINATION HELPER
# ============================================================
def pages(service,path,params=None,store_id=None):
    out=[]
    for p in range(100):
        q=dict(params or {});q.update(page=p,size=100);d=get(service,path,q,store_id=store_id); a=d.get('content') if isinstance(d,dict) else d; a=a if isinstance(a,list) else []; out+=a
        if not d.get('nextPageToken') if isinstance(d,dict) else True:break
    return out

# ============================================================
# SYNCHRONIZATION
# ============================================================
def sync_products(store_id=None):
    p=store_or_default(store_id)
    if not p or p['platform']!='trendyol':raise HTTPException(400,'Bu mağaza için Trendyol senkronizasyonu aktif değil.')
    if not p['seller_id']:raise HTTPException(400,f"{p['name']} için Seller ID eksik.")
    items=pages('product',f"sellers/{p['seller_id']}/products/approved",store_id=store_id); inv=pages('product',f"sellers/{p['seller_id']}/products/approved/inventory-and-price",store_id=store_id);t=nowms();con=db()
    for x in items:
        rawcc=str(x.get('contentId') or ''); cc=f'{store_id}::{rawcc}'; imgs=x.get('images') or []; img=imgs[0].get('url','') if imgs and isinstance(imgs[0],dict) else ''
        con.execute('INSERT OR REPLACE INTO products(store_id,content_id,product_main_id,title,category_id,category_name,brand_id,description,image_url,status,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(store_id,cc,x.get('productMainId',''),x.get('title',''),si(x.get('categoryId')),x.get('categoryName',''),si(x.get('brandId')),x.get('description',''),img,x.get('status','approved'),t))
    for x in inv:
        for v in x.get('variants') or []:
            rawbc=str(v.get('barcode') or '')
            if rawbc:
                con.execute('INSERT OR REPLACE INTO variants(store_id,content_id,variant_id,barcode,stock_code,product_main_id,quantity,sale_price,list_price,status,stock_modified_at,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(store_id,f'{store_id}::{str(x.get("contentId") or "")}',str(v.get('variantId') or ''),f'{store_id}::{rawbc}',v.get('stockCode',''),x.get('productMainId',''),sf(v.get('quantity')) or 0,sf(v.get('salePrice')) or 0,sf(v.get('listPrice')) or 0,v.get('status',''),si(v.get('stockLastModifiedDate')),t))
    con.execute('INSERT INTO sync_log(store_id,sync_type,finished_at,status,message) VALUES(?,?,?,?,?)',(store_id,'products',t,'OK',f'{len(items)} content'));con.commit();con.close();return {'ok':True,'storeId':store_id,'storeName':p['name'],'count':len(items),'message':f'{len(items)} ürün senkronize edildi.'}

def order_content(d):return d.get('content',[]) if isinstance(d,dict) and isinstance(d.get('content'),list) else d if isinstance(d,list) else []
def sync_orders(days=14,store_id=None):
    p=store_or_default(store_id)
    if not p or p['platform']!='trendyol':raise HTTPException(400,'Bu mağaza için Trendyol senkronizasyonu aktif değil.')
    if not p['seller_id']:raise HTTPException(400,f"{p['name']} için Seller ID eksik.")
    end=datetime.now(timezone.utc);start=end-timedelta(days=min(max(days,1),14));all=[];cur=start
    while cur<end:
        ce=min(cur+timedelta(days=13,hours=23,minutes=59),end);d=get('order',f"sellers/{p['seller_id']}/v2/orders",{'startDate':int(cur.timestamp()*1000),'endDate':int(ce.timestamp()*1000),'orderByField':'PackageLastModifiedDate','orderByDirection':'DESC','size':50},store_id=store_id);all+=order_content(d);cur=ce+timedelta(milliseconds=1)
    con=db();t=nowms()
    for o in all:
        rawpid=str(o.get('shipmentPackageId') or o.get('id') or ''); pid=f'{store_id}::{rawpid}'
        if not rawpid:continue
        con.execute('INSERT OR REPLACE INTO orders(store_id,shipment_package_id,order_number,status,cargo_provider,order_date,last_modified_date,total_price,raw_json,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(store_id,pid,str(o.get('orderNumber','')),str(o.get('status','')),str(o.get('cargoProviderName') or o.get('cargoProvider') or ''),si(o.get('orderDate') or o.get('createdDate')),si(o.get('packageLastModifiedDate') or o.get('lastModifiedDate')),sf(o.get('totalPrice') or o.get('grossAmount')) or 0,json.dumps(o,ensure_ascii=False),t))
        for l in o.get('lines') or o.get('orderLines') or []:
            lid=str(l.get('id') or l.get('orderLineId') or '');con.execute('INSERT OR REPLACE INTO order_items(store_id,shipment_package_id,order_line_id,barcode,product_name,quantity,price,status,raw_json,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(store_id,pid,str(lid),str(l.get('barcode') or l.get('stockCode') or ''),str(l.get('productName') or ''),sf(l.get('quantity')) or 0,sf(l.get('price') or l.get('amount')) or 0,str(l.get('status') or ''),json.dumps(l,ensure_ascii=False),t))
    con.execute('INSERT INTO sync_log(store_id,sync_type,finished_at,status,message) VALUES(?,?,?,?,?)',(store_id,'orders',t,'OK',f'{len(all)} paket'));con.commit();con.close();return {'ok':True,'storeId':store_id,'storeName':p['name'],'count':len(all),'message':f'{len(all)} sipariş paketi senkronize edildi.'}

def sync_finance(days=30,store_id=None):
    p=store_or_default(store_id)
    if not p or p['platform']!='trendyol':raise HTTPException(400,'Bu mağaza için Trendyol senkronizasyonu aktif değil.')
    if not p['seller_id']:raise HTTPException(400,f"{p['name']} için Seller ID eksik.")
    end=datetime.now(timezone.utc);start=end-timedelta(days=min(max(days,1),365));all=[];cur=start
    while cur<end:
        ce=min(cur+timedelta(days=14),end)
        for typ in ('Sale','Return'):
            d=get('finance',f"che/sellers/{p['seller_id']}/settlements",{'transactionType':typ,'startDate':int(cur.timestamp()*1000),'endDate':int(ce.timestamp()*1000),'page':0,'size':500},store_id=store_id);all+=(d.get('content',[]) if isinstance(d,dict) else [])
        cur=ce+timedelta(milliseconds=1)
    con=db();t=nowms()
    for x in all:
        rawtid=str(x.get('id') or x.get('transactionId') or '|'.join(map(str,[x.get('orderNumber',''),x.get('barcode',''),x.get('transactionDate',''),x.get('transactionType','')])))
        tid=f'{store_id}::{rawtid}'
        con.execute('INSERT OR REPLACE INTO settlements(store_id,transaction_id,transaction_type,order_number,barcode,transaction_date,seller_revenue,commission,amount,raw_json,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(store_id,tid,str(x.get('transactionType') or ''),str(x.get('orderNumber') or ''),str(x.get('barcode') or ''),si(x.get('transactionDate') or x.get('transactionDateTime')),sf(x.get('sellerRevenue')) or 0,sf(x.get('commission')) or 0,sf(x.get('amount') or x.get('sellerRevenue')) or 0,json.dumps(x,ensure_ascii=False),t))
    con.execute('INSERT INTO sync_log(store_id,sync_type,finished_at,status,message) VALUES(?,?,?,?,?)',(store_id,'finance',t,'OK',f'{len(all)} finans işlemi'));con.commit();con.close();return {'ok':True,'storeId':store_id,'storeName':p['name'],'count':len(all),'message':f'{len(all)} finans işlemi senkronize edildi.'}

@app.post('/sync/products')
def sp(store_id:str=''):return sync_products(store_id)
@app.post('/sync/orders')
def so(days:int=14,store_id:str=''):return sync_orders(days,store_id)
@app.post('/sync/finance')
def sf_route(days:int=30,store_id:str=''):return sync_finance(days,store_id)
@app.post('/sync/all')
def sa(days:int=30,store_id:str=''):
    r={};e={}
    for n,fn in [('products',lambda:sync_products(store_id)),('orders',lambda:sync_orders(min(days,14),store_id)),('finance',lambda:sync_finance(days,store_id))]:
        try:r[n]=fn()
        except Exception as x:e[n]=str(x)
    return {'ok':not e,'storeId':store_id,'results':r,'errors':e}

# ============================================================
# DASHBOARD + LİSTELER
# ============================================================
def rng(p):
    n=datetime.now();mp={'today':0,'7d':7,'30d':30,'3m':90,'6m':180,'1y':365};d=mp.get(p,30);s=datetime(n.year,n.month,n.day) if p=='today' else n-timedelta(days=d);return int(s.timestamp()*1000),int(n.timestamp()*1000)

@app.get('/api/dashboard')
def dash(period:str='30d',store_id:str=''):
    s,e=rng(period);c=db();sales=c.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) a FROM settlements WHERE store_id=? AND transaction_type='Sale' AND transaction_date>=? AND transaction_date<?",(store_id,s,e)).fetchone();ret=c.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) a FROM settlements WHERE store_id=? AND transaction_type='Return' AND transaction_date>=? AND transaction_date<?",(store_id,s,e)).fetchone();orders=c.execute('SELECT COUNT(*) n FROM orders WHERE store_id=? AND order_date>=? AND order_date<?',(store_id,s,e)).fetchone()['n'];cancel=c.execute("SELECT COUNT(*) n FROM orders WHERE store_id=? AND order_date>=? AND order_date<? AND LOWER(status) LIKE '%cancel%'",(store_id,s,e)).fetchone()['n'];p=c.execute('SELECT COUNT(*) n FROM products WHERE store_id=?',(store_id,)).fetchone()['n'];v=c.execute('SELECT COUNT(*) n,COALESCE(SUM(quantity),0) stock FROM variants WHERE store_id=?',(store_id,)).fetchone();low=c.execute('SELECT COUNT(*) n FROM variants WHERE store_id=? AND quantity<=5',(store_id,)).fetchone()['n'];top=c.execute('''SELECT COALESCE(NULLIF(oi.product_name,''),oi.barcode) name,COALESCE(SUM(oi.quantity),0) qty,COALESCE(SUM(oi.price*oi.quantity),0) revenue FROM order_items oi JOIN orders o ON o.shipment_package_id=oi.shipment_package_id WHERE oi.store_id=? AND o.store_id=? AND o.order_date>=? AND o.order_date<? GROUP BY name ORDER BY qty DESC LIMIT 10''',(store_id,store_id,s,e)).fetchall();c.close();sa=float(sales['a'] or 0);ra=float(ret['a'] or 0);return {'cards':{'products':p,'variants':v['n'],'stock':v['stock'],'low_stock':low,'orders':orders,'sales':sa,'returns':ra,'net':sa-ra,'return_rate':round((ret['n']/(sales['n'] or 1))*100,2),'cancel_rate':round((cancel/(orders or 1))*100,2)},'top':[dict(x) for x in top]}

@app.get('/api/products')
def products(q:str='',limit:int=200,store_id:str=''):
    c=db();x=f'%{q}%';r=c.execute("SELECT v.product_main_id,CASE WHEN instr(v.barcode,'::')>0 THEN substr(v.barcode,instr(v.barcode,'::')+2) ELSE v.barcode END barcode,v.quantity,v.sale_price,v.status,p.title,p.category_name FROM variants v LEFT JOIN products p ON p.content_id=v.content_id WHERE v.store_id=? AND (?='' OR v.barcode LIKE ? OR v.product_main_id LIKE ? OR p.title LIKE ?) ORDER BY v.product_main_id,v.barcode LIMIT ?",(store_id,q,x,x,x,min(limit,1000))).fetchall();c.close();return {'items':[dict(z) for z in r]}

@app.get('/api/orders')
def orders(q:str='',limit:int=200,store_id:str=''):
    c=db();x=f'%{q}%';r=c.execute("SELECT CASE WHEN instr(shipment_package_id,'::')>0 THEN substr(shipment_package_id,instr(shipment_package_id,'::')+2) ELSE shipment_package_id END shipment_package_id,order_number,status,cargo_provider,order_date,total_price FROM orders WHERE store_id=? AND (?='' OR order_number LIKE ? OR shipment_package_id LIKE ? OR status LIKE ?) ORDER BY order_date DESC LIMIT ?",(store_id,q,x,x,x,min(limit,1000))).fetchall();c.close();return {'items':[dict(z) for z in r]}

@app.get('/api/finance')
def finance(t:str='',limit:int=300,store_id:str=''):
    c=db();r=c.execute('SELECT transaction_type,order_number,barcode,transaction_date,amount FROM settlements '+('WHERE store_id=? AND transaction_type=? ' if t else 'WHERE store_id=? ' )+'ORDER BY transaction_date DESC LIMIT ?',((store_id,t,min(limit,1000)) if t else (store_id,min(limit,1000),))).fetchall();c.close();return {'items':[dict(z) for z in r]}

# ============================================================
# YENİ: INVENTORY UPDATE
# ============================================================
def update_inventory(items, store_id=None):
    p=store_or_default(store_id)
    if not p or p['platform']!='trendyol':raise HTTPException(400,'Bu mağaza için Trendyol senkronizasyonu aktif değil.')
    if not p['seller_id']:raise HTTPException(400,f"{p['name']} için Seller ID eksik.")
    if not items:raise HTTPException(400,'Güncellenecek ürün yok.')
    if len(items)>1000:raise HTTPException(400,'Tek seferde en fazla 1000 varyant güncellenebilir.')
    clean=[]
    for x in items:
        bc=str(x.get('barcode') or '').strip()
        if not bc:continue
        if '::' in bc:bc=bc.split('::',1)[1]
        row={'barcode':bc}
        if x.get('quantity') is not None:
            try:row['quantity']=int(x['quantity'])
            except (ValueError,TypeError):raise HTTPException(400,f"Geçersiz stok: {x.get('quantity')}")
        if x.get('salePrice') is not None:
            try:row['salePrice']=float(str(x['salePrice']).replace(',','.'))
            except (ValueError,TypeError):raise HTTPException(400,f"Geçersiz satış fiyatı: {x.get('salePrice')}")
        if x.get('listPrice') is not None:
            try:row['listPrice']=float(str(x['listPrice']).replace(',','.'))
            except (ValueError,TypeError):raise HTTPException(400,f"Geçersiz liste fiyatı: {x.get('listPrice')}")
        clean.append(row)
    if not clean:raise HTTPException(400,'Geçerli barkod bulunamadı.')
    payload={'items':clean}
    result=put('inventory',f"sellers/{p['seller_id']}/products/price-and-inventory",payload,store_id=store_id)
    batch_id=result.get('batchRequestId') if isinstance(result,dict) else None
    t=nowms();con=db()
    for x in clean:
        prefixed=f'{store_id}::{x["barcode"]}'
        fields=[];params=[]
        if 'quantity' in x:fields.append('quantity=?');params.append(x['quantity'])
        if 'salePrice' in x:fields.append('sale_price=?');params.append(x['salePrice'])
        if 'listPrice' in x:fields.append('list_price=?');params.append(x['listPrice'])
        if fields:
            fields.append('synced_at=?');params.append(t);params.append(prefixed)
            con.execute(f"UPDATE variants SET {', '.join(fields)} WHERE barcode=?",params)
    con.execute('INSERT INTO sync_log(store_id,sync_type,finished_at,status,message) VALUES(?,?,?,?,?)',(store_id,'inventory_update',t,'OK',f'{len(clean)} varyant'))
    con.commit();con.close()
    return {'ok':True,'storeId':store_id,'storeName':p['name'],'updatedCount':len(clean),'batchRequestId':batch_id,'trendyol':result,'message':f'{len(clean)} varyant güncellendi.'}

@app.post('/api/inventory/update')
async def api_inventory_update(request:Request):
    try:body=await request.json()
    except Exception:raise HTTPException(400,'Geçersiz JSON gövdesi.')
    store_id=str(body.get('store_id') or DEFAULT_STORE_ID).strip()
    items=body.get('items') or []
    if not isinstance(items,list):raise HTTPException(400,'items bir liste olmalıdır.')
    return update_inventory(items,store_id)

@app.post('/api/inventory/update-single')
async def api_inventory_update_single(request:Request):
    try:body=await request.json()
    except Exception:raise HTTPException(400,'Geçersiz JSON gövdesi.')
    store_id=str(body.get('store_id') or DEFAULT_STORE_ID).strip()
    barcode=str(body.get('barcode') or '').strip()
    if not barcode:raise HTTPException(400,'Barkod zorunlu.')
    item={'barcode':barcode}
    if body.get('quantity') is not None:item['quantity']=body['quantity']
    if body.get('salePrice') is not None:item['salePrice']=body['salePrice']
    if body.get('listPrice') is not None:item['listPrice']=body['listPrice']
    if len(item)<2:raise HTTPException(400,'En az bir alan gönderilmeli.')
    return update_inventory([item],store_id)

@app.post('/api/inventory/bulk-adjust')
async def api_inventory_bulk_adjust(request:Request):
    try:body=await request.json()
    except Exception:raise HTTPException(400,'Geçersiz JSON gövdesi.')
    store_id=str(body.get('store_id') or DEFAULT_STORE_ID).strip()
    flt=body.get('filter') or {};op=body.get('operation') or {}
    con=db();where=['store_id=?'];params=[store_id]
    if flt.get('product_main_id'):where.append('product_main_id=?');params.append(flt['product_main_id'])
    if flt.get('barcode_contains'):where.append("barcode LIKE ?");params.append(f"%{flt['barcode_contains']}%")
    if flt.get('max_quantity') is not None:where.append('quantity<=?');params.append(float(flt['max_quantity']))
    if flt.get('min_quantity') is not None:where.append('quantity>=?');params.append(float(flt['min_quantity']))
    rows=con.execute(f"SELECT barcode,quantity,sale_price,list_price FROM variants WHERE {' AND '.join(where)} LIMIT 1000",params).fetchall()
    if not rows:con.close();raise HTTPException(404,'Filtreye uyan varyant bulunamadı.')
    items=[]
    for r in rows:
        item={'barcode':r['barcode']};new_q=r['quantity'];new_s=r['sale_price'];new_l=r['list_price']
        if op.get('set_quantity') is not None:new_q=int(op['set_quantity'])
        elif op.get('add_quantity') is not None:new_q=max(0,int((r['quantity'] or 0)+op['add_quantity']))
        if op.get('set_salePrice') is not None:new_s=float(str(op['set_salePrice']).replace(',','.'))
        elif op.get('multiply_price') is not None:new_s=round(float(r['sale_price'] or 0)*float(op['multiply_price']),2)
        if op.get('set_listPrice') is not None:new_l=float(str(op['set_listPrice']).replace(',','.'))
        item['quantity']=int(new_q);item['salePrice']=float(new_s);item['listPrice']=float(new_l or new_s)
        items.append(item)
    con.close()
    result=update_inventory(items,store_id);result['filteredCount']=len(items);return result

# ============================================================
# YENİ: ADVANCED REPORTS
# ============================================================
@app.get('/api/reports/sales-by-day')
def report_sales_by_day(period:str='30d',store_id:str=''):
    s,e=rng(period);c=db()
    rows=c.execute("""SELECT date(transaction_date/1000,'unixepoch') d,
                  COALESCE(SUM(CASE WHEN transaction_type='Sale' THEN amount ELSE 0 END),0) sales,
                  COALESCE(SUM(CASE WHEN transaction_type='Return' THEN amount ELSE 0 END),0) returns,
                  COUNT(CASE WHEN transaction_type='Sale' THEN 1 END) sale_count,
                  COUNT(CASE WHEN transaction_type='Return' THEN 1 END) return_count
                  FROM settlements WHERE store_id=? AND transaction_date>=? AND transaction_date<?
                  GROUP BY d ORDER BY d""",(store_id,s,e)).fetchall()
    c.close();return {'items':[dict(x) for x in rows]}

@app.get('/api/reports/top-products')
def report_top_products(period:str='30d',limit:int=50,store_id:str=''):
    s,e=rng(period);c=db()
    rows=c.execute("""SELECT COALESCE(NULLIF(oi.product_name,''),oi.barcode) name,oi.barcode barcode,
                  COALESCE(SUM(oi.quantity),0) qty,COALESCE(SUM(oi.price*oi.quantity),0) revenue,
                  COUNT(DISTINCT oi.shipment_package_id) orders
                  FROM order_items oi JOIN orders o ON o.shipment_package_id=oi.shipment_package_id
                  WHERE oi.store_id=? AND o.store_id=? AND o.order_date>=? AND o.order_date<?
                  GROUP BY name,oi.barcode ORDER BY qty DESC LIMIT ?""",(store_id,store_id,s,e,min(limit,500))).fetchall()
    c.close();return {'items':[dict(x) for x in rows]}

@app.get('/api/reports/low-stock')
def report_low_stock(threshold:int=5,store_id:str=''):
    c=db();rows=c.execute("""SELECT v.product_main_id,v.barcode,v.quantity,v.sale_price,p.title
           FROM variants v LEFT JOIN products p ON p.content_id=v.content_id
           WHERE v.store_id=? AND v.quantity<=? ORDER BY v.quantity ASC LIMIT 500""",(store_id,threshold)).fetchall()
    c.close();return {'items':[dict(x) for x in rows]}

@app.get('/api/reports/store-summary')
def report_store_summary(period:str='30d',store_id:str=''):
    s,e=rng(period);c=db()
    sales=c.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) a FROM settlements WHERE store_id=? AND transaction_type='Sale' AND transaction_date>=? AND transaction_date<?",(store_id,s,e)).fetchone()
    returns=c.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) a FROM settlements WHERE store_id=? AND transaction_type='Return' AND transaction_date>=? AND transaction_date<?",(store_id,s,e)).fetchone()
    orders=c.execute('SELECT COUNT(*) n,COALESCE(SUM(total_price),0) a FROM orders WHERE store_id=? AND order_date>=? AND order_date<?',(store_id,s,e)).fetchone()
    cancelled=c.execute("SELECT COUNT(*) n FROM orders WHERE store_id=? AND order_date>=? AND order_date<? AND LOWER(status) LIKE '%cancel%'",(store_id,s,e)).fetchone()['n']
    total_stock=c.execute('SELECT COALESCE(SUM(quantity),0) s FROM variants WHERE store_id=?',(store_id,)).fetchone()['s']
    product_count=c.execute('SELECT COUNT(DISTINCT product_main_id) n FROM variants WHERE store_id=?',(store_id,)).fetchone()['n']
    c.close();sa=float(sales['a'] or 0);ra=float(returns['a'] or 0)
    return {'storeId':store_id,'storeName':store_label(store_id),'period':period,
        'sales':{'count':sales['n'],'amount':sa},
        'returns':{'count':returns['n'],'amount':ra},
        'net':sa-ra,
        'orders':{'count':orders['n'],'amount':float(orders['a'] or 0),'cancelled':cancelled},
        'stock':{'total_units':float(total_stock or 0),'product_count':product_count},
        'return_rate':round((returns['n']/(sales['n'] or 1))*100,2),
        'cancel_rate':round((cancelled/(orders['n'] or 1))*100,2)}

@app.get('/api/reports/barcode-history')
def report_barcode_history(barcode:str='',limit:int=100,store_id:str=''):
    if not barcode.strip():raise HTTPException(400,'barcode zorunlu.')
    bc=barcode.strip();bc_prefixed=bc if '::' in bc else f'{store_id}::{bc}'
    c=db();rows=c.execute("""SELECT transaction_type,order_number,transaction_date,amount,seller_revenue,commission
           FROM settlements WHERE store_id=? AND barcode=? ORDER BY transaction_date DESC LIMIT ?""",(store_id,bc_prefixed,min(limit,500))).fetchall()
    c.close();return {'barcode':barcode,'items':[dict(x) for x in rows]}

@app.get('/api/reports/export-csv')
def report_export_csv(kind:str='products',period:str='30d',store_id:str=''):
    import csv,io
    if kind=='products':
        c=db();rows=c.execute("""SELECT v.product_main_id,v.barcode,v.quantity,v.sale_price,v.list_price,v.status,
                      COALESCE(p.title,'') title,COALESCE(p.category_name,'') category_name
               FROM variants v LEFT JOIN products p ON p.content_id=v.content_id
               WHERE v.store_id=? ORDER BY v.product_main_id,v.barcode""",(store_id,)).fetchall();c.close()
        headers=['Model','Barkod','Stok','Satış Fiyatı','Liste Fiyatı','Durum','Başlık','Kategori']
        data=[[r['product_main_id'],r['barcode'],r['quantity'],r['sale_price'],r['list_price'],r['status'],r['title'],r['category_name']] for r in rows]
        fname=f'urunler_{store_id}_{datetime.now().strftime("%Y%m%d")}.csv'
    elif kind=='orders':
        s,e=rng(period);c=db()
        rows=c.execute("""SELECT order_number,shipment_package_id,status,cargo_provider,order_date,total_price
               FROM orders WHERE store_id=? AND order_date>=? AND order_date<? ORDER BY order_date DESC""",(store_id,s,e)).fetchall();c.close()
        headers=['Sipariş No','Paket ID','Durum','Kargo','Tarih','Tutar']
        data=[[r['order_number'],r['shipment_package_id'],r['status'],r['cargo_provider'],datetime.fromtimestamp((r['order_date'] or 0)/1000).strftime('%Y-%m-%d %H:%M'),r['total_price']] for r in rows]
        fname=f'siparisler_{store_id}_{period}_{datetime.now().strftime("%Y%m%d")}.csv'
    elif kind=='finance':
        s,e=rng(period);c=db()
        rows=c.execute("""SELECT transaction_type,order_number,barcode,transaction_date,amount,seller_revenue,commission
               FROM settlements WHERE store_id=? AND transaction_date>=? AND transaction_date<? ORDER BY transaction_date DESC""",(store_id,s,e)).fetchall();c.close()
        headers=['Tip','Sipariş','Barkod','Tarih','Tutar','Satıcı Geliri','Komisyon']
        data=[[r['transaction_type'],r['order_number'],r['barcode'],datetime.fromtimestamp((r['transaction_date'] or 0)/1000).strftime('%Y-%m-%d %H:%M'),r['amount'],r['seller_revenue'],r['commission']] for r in rows]
        fname=f'finans_{store_id}_{period}_{datetime.now().strftime("%Y%m%d")}.csv'
    else:
        raise HTTPException(400,f'Bilinmeyen rapor: {kind}')
    buf=io.StringIO();w=csv.writer(buf,delimiter=';');w.writerow(headers)
    for row in data:w.writerow(row)
    content='\ufeff'+buf.getvalue()
    return Response(content=content,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{fname}"'})
    # ============================================================
# FRONTEND PAGE
# ============================================================
PAGE=r'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ayşe Tesettür V5.2</title><style>
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;font-family:Arial;color:#222}
.wrap{max-width:1250px;margin:25px auto;padding:0 15px}
.card{background:#fff;border:1px solid #ddd;border-radius:14px;padding:20px;margin-bottom:16px}
h1{margin:0 0 6px}h2{font-size:19px;border-bottom:1px solid #eee;padding-bottom:10px}
.nav{display:flex;gap:7px;flex-wrap:wrap}
.nav button{padding:10px;border:0;border-radius:8px}
.nav .on{background:#111;color:#fff}
.tab{display:none}.tab.on{display:block}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.full{grid-column:1/-1}
input,select,textarea{width:100%;padding:10px;border:1px solid #ccc;border-radius:8px}
textarea{min-height:90px}
label{display:block;font-weight:700;margin:9px 0 5px}
button{cursor:pointer;border:0;border-radius:8px;padding:10px 14px}
.primary{background:#111;color:#fff}
.green{background:#147a38;color:#fff}
.red{background:#b42318;color:#fff}
.muted,.hint{font-size:13px;color:#777}
.fixed,.selected{background:#f7fbff;border:1px solid #d7e9ff;border-radius:10px;padding:12px;line-height:1.8}
.selected{display:none;background:#eef8f0;border-color:#b9dfc1}
.search{display:flex;gap:8px}.search input{flex:1}
.cat{display:block;width:100%;text-align:left;background:#fafafa;border:1px solid #ddd;margin-top:7px}
.color{border:1px solid #ddd;border-radius:12px;padding:14px;margin-top:12px}
.colorhead{display:flex;justify-content:space-between}
.preview{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:9px;margin-top:10px}
.thumb{border:2px solid #ddd;border-radius:9px;padding:5px;cursor:pointer}
.thumb.main{border-color:#147a38}
.thumb img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px}
.badge{text-align:center;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
.kpi{background:#fafafa;border:1px solid #ddd;border-radius:10px;padding:12px}
.kpi b{font-size:22px;display:block;margin-top:4px}
table{width:100%;border-collapse:collapse}
th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}
.scroll{overflow:auto;max-height:600px}
.msg{white-space:pre-wrap;margin-top:10px;padding:10px;border-radius:8px;background:#eef8f0}
.err{background:#fff1f0}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
.toolbar input{max-width:220px}
.inv-qty,.inv-price{width:90px}
@media(max-width:800px){.grid,.g2,.kpis{grid-template-columns:1fr 1fr}}
@media(max-width:600px){.grid,.g2,.kpis{grid-template-columns:1fr}}
</style></head><body><div class="wrap">

<div class="card">
  <h1>Ayşe Tesettür - Trendyol Management System V5.2</h1>
  <div class="muted">Ürün yükleme + ürünler + siparişler + satış/iade + <b>stok güncelleme</b> + <b>raporlar</b></div>
  <div class="nav" style="margin-top:12px">
    <button class="on" onclick="tab('dash',this)">Dashboard</button>
    <button onclick="tab('upload',this)">Ürün Yükle</button>
    <button onclick="tab('products',this)">Ürünler</button>
    <button onclick="tab('inventory',this)">Stok / Fiyat</button>
    <button onclick="tab('orders',this)">Siparişler</button>
    <button onclick="tab('finance',this)">Satış / İade</button>
    <button onclick="tab('reports',this)">Raporlar</button>
    <button onclick="tab('sync',this)">Senkronizasyon</button>
  </div>
  <div style="margin-top:12px">
    <label style="margin-top:0">Mağaza Seç</label>
    <select id="storeSelect"></select>
    <div id="storeInfo" class="hint" style="margin-top:6px"></div>
  </div>
</div>

<!-- ============ DASHBOARD ============ -->
<section id="dash" class="tab on">
  <div class="card">
    <label>Dönem</label>
    <select id="period" onchange="loadDash()">
      <option value="today">Bugün</option>
      <option value="7d">7 Gün</option>
      <option value="30d" selected>30 Gün</option>
      <option value="3m">3 Ay</option>
      <option value="6m">6 Ay</option>
      <option value="1y">1 Yıl</option>
    </select>
  </div>
  <div id="kpis" class="kpis"></div>
  <div class="card"><h2>En Çok Satanlar</h2>
    <div class="scroll"><table><tbody id="top"></tbody></table></div>
  </div>
</section>

<!-- ============ UPLOAD ============ -->
<section id="upload" class="tab">
  <form id="form" enctype="multipart/form-data">
    <div class="card"><h2>Sabit Mağaza</h2>
      <div class="fixed">
        <b>Marka:</b> <span id="brandLabel">Ayşe Tesettür</span><br>
        <b>Menşei:</b> Türkiye<br>
        <b>Cinsiyet:</b> Kadın / Kız<br>
        <b>Trendyol Kumaş Tipi:</b> Dokuma
      </div>
    </div>
    <div class="card"><h2>Kategori</h2>
      <div class="search"><input id="cq" placeholder="Tesettür Abiye"><button type="button" onclick="searchCat()">Ara</button></div>
      <div id="cats"></div>
      <div id="cm" class="hint"></div>
      <div id="sel" class="selected"><b>Seçilen:</b> <span id="selt"></span></div>
      <input type="hidden" name="category_id" id="cid">
      <input type="hidden" name="category_name" id="cname">
    </div>
    <div class="card"><h2>Ürün Bilgileri</h2>
      <div class="grid">
        <div><label>Model Kodu</label><input name="model_code" value="narin15800" required></div>
        <div><label>Fiyat</label><input name="price" value="7299" required></div>
        <div><label>Stok / Beden</label><input name="stock" value="1000" required></div>
        <div><label>KDV</label><select name="vat_rate"><option value="20">%20</option><option value="10">%10</option><option value="1">%1</option><option value="0">%0</option></select></div>
        <div class="full"><label>Başlık</label><input name="title" value="Tesettür Abiye Uzun Gece Elbisesi" required></div>
        <div><label>Gerçek Kumaş</label><input name="actual_fabric" value="Şifon"></div>
        <div><label>Bedenler</label><input name="sizes" value="42,44,46,48,50,52" required></div>
        <div class="full"><label>Açıklama</label><textarea name="description"></textarea></div>
      </div>
    </div>
    <div class="card"><h2>Renkler ve Fotoğraflar</h2>
      <div class="hint">Her renk için Trendyol canlı rengini seç. 1-5 fotoğraf. Fotoğrafa tıklarsan ana fotoğraf olur.</div>
      <input type="hidden" name="color_count" id="cc" value="0">
      <div id="colors"></div>
      <button type="button" class="green" onclick="addColor()">+ Renk Ekle</button>
    </div>
    <div class="card">
      <button class="primary" style="width:100%;font-size:17px" id="send">Seçili Mağazaya Gönder</button>
      <div id="um" class="msg" style="display:none"></div>
    </div>
  </form>
</section>

<!-- ============ PRODUCTS ============ -->
<section id="products" class="tab">
  <div class="card"><h2>Ürünler</h2>
    <input id="pq" placeholder="Barkod / model / başlık" oninput="loadProducts()">
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>Model</th><th>Barkod</th><th>Ürün</th><th>Stok</th><th>Fiyat</th></tr></thead>
      <tbody id="pt"></tbody>
    </table></div>
  </div>
</section>

<!-- ============ INVENTORY ============ -->
<section id="inventory" class="tab">
  <div class="card">
    <h2>Stok / Fiyat Güncelleme</h2>
    <input id="iq" placeholder="Barkod / model / başlık ara" oninput="loadInventory()">
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr>
        <th style="width:40px"><input type="checkbox" id="invAll" onchange="toggleAllInv(this)"></th>
        <th>Model</th><th>Barkod</th><th>Ürün</th>
        <th>Stok</th><th>Fiyat</th><th>Durum</th><th>İşlem</th>
      </tr></thead>
      <tbody id="invTable"></tbody>
    </table></div>
    <div class="toolbar">
      <input id="bulkQty" type="number" placeholder="Toplu değer">
      <button class="primary" onclick="bulkApply('quantity')">Seçiliye stok uygula</button>
      <button class="primary" onclick="bulkApply('salePrice')">Seçiliye fiyat uygula</button>
      <button class="green" onclick="saveSelected()">Seçiliyi kaydet</button>
      <button class="red" onclick="clearSelection()">Seçimi temizle</button>
    </div>
    <div id="invMsg" class="msg" style="display:none"></div>
  </div>

  <div class="card">
    <h2>Filtreli Toplu İşlem (Bulk Adjust)</h2>
    <div class="hint">Örn: model kodu = narin15800, stok ≤ 10 olanları stok = 50 yap.</div>
    <div class="g2" style="margin-top:10px">
      <div><label>Model Kodu</label><input id="bModel" placeholder="narin15800"></div>
      <div><label>Barkod içerir</label><input id="bBarcode" placeholder="narin"></div>
      <div><label>Max Stok</label><input id="bMaxQ" type="number" placeholder="10"></div>
      <div><label>Min Stok</label><input id="bMinQ" type="number" placeholder="0"></div>
      <div><label>Stok ayarla (=)</label><input id="bSetQ" type="number" placeholder="50"></div>
      <div><label>Stok ekle (+/-)</label><input id="bAddQ" type="number" placeholder="-5"></div>
      <div><label>Fiyat ayarla (=)</label><input id="bSetP" type="number" step="0.01" placeholder="999.90"></div>
      <div><label>Fiyat çarpan (×)</label><input id="bMulP" type="number" step="0.01" placeholder="1.10"></div>
    </div>
    <div class="toolbar">
      <button class="red" onclick="bulkAdjust()">Filtrelenmişi Güncelle</button>
    </div>
    <div id="bulkMsg" class="msg" style="display:none"></div>
  </div>
</section>

<!-- ============ ORDERS ============ -->
<section id="orders" class="tab">
  <div class="card"><h2>Siparişler</h2>
    <input id="oq" placeholder="Sipariş / paket" oninput="loadOrders()">
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>Tarih</th><th>Sipariş</th><th>Paket</th><th>Durum</th><th>Tutar</th></tr></thead>
      <tbody id="ot"></tbody>
    </table></div>
  </div>
</section>

<!-- ============ FINANCE ============ -->
<section id="finance" class="tab">
  <div class="card"><h2>Satış / İade</h2>
    <div class="toolbar">
      <button class="green" onclick="loadFinance('Sale')">Satış</button>
      <button class="red" onclick="loadFinance('Return')">İade</button>
      <button onclick="loadFinance('')">Tümü</button>
    </div>
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>Tarih</th><th>Tip</th><th>Sipariş</th><th>Barkod</th><th>Tutar</th></tr></thead>
      <tbody id="ft"></tbody>
    </table></div>
  </div>
</section>

<!-- ============ REPORTS ============ -->
<section id="reports" class="tab">
  <div class="card">
    <h2>Raporlar</h2>
    <div class="toolbar">
      <select id="rPeriod" onchange="refreshReports()">
        <option value="today">Bugün</option>
        <option value="7d">7 Gün</option>
        <option value="30d" selected>30 Gün</option>
        <option value="3m">3 Ay</option>
        <option value="6m">6 Ay</option>
        <option value="1y">1 Yıl</option>
      </select>
      <button onclick="refreshReports()">Yenile</button>
      <button class="green" onclick="exportCSV('products')">Ürünler CSV</button>
      <button class="green" onclick="exportCSV('orders')">Siparişler CSV</button>
      <button class="green" onclick="exportCSV('finance')">Finans CSV</button>
    </div>
  </div>

  <div class="card"><h2>Mağaza Özeti</h2><div id="rSummary" class="kpis"></div></div>

  <div class="card"><h2>Günlük Satış</h2>
    <div class="scroll"><table>
      <thead><tr><th>Tarih</th><th>Satış</th><th>İade</th><th>Net</th><th>#Satış</th><th>#İade</th></tr></thead>
      <tbody id="rDaily"></tbody>
    </table></div>
  </div>

  <div class="card"><h2>En Çok Satanlar</h2>
    <div class="scroll"><table>
      <thead><tr><th>#</th><th>Ürün</th><th>Barkod</th><th>Adet</th><th>Ciro</th><th>Sipariş</th></tr></thead>
      <tbody id="rTop"></tbody>
    </table></div>
  </div>

  <div class="card"><h2>Düşük Stok</h2>
    <div class="toolbar">
      <input id="rThreshold" type="number" value="5">
      <button onclick="loadLowStock()">Uygula</button>
    </div>
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>Model</th><th>Barkod</th><th>Ürün</th><th>Stok</th><th>Fiyat</th></tr></thead>
      <tbody id="rLow"></tbody>
    </table></div>
  </div>
</section>

<!-- ============ SYNC ============ -->
<section id="sync" class="tab">
  <div class="card"><h2>Senkronizasyon — Seçili Mağaza</h2>
    <div class="toolbar">
      <button onclick="sync('products')">Ürünler</button>
      <button onclick="sync('orders')">Siparişler</button>
      <button onclick="sync('finance')">Satış / İade</button>
      <button class="green" onclick="sync('all')">HEPSİ</button>
    </div>
    <div id="sm" class="msg" style="display:none"></div>
  </div>
</section>

</div>
<script>
const $=x=>document.getElementById(x);
let colors=[],available=[],source={},row=0,invRows=[];

function currentStore(){return $('storeSelect')?.value||'trendyol_ayse'}
function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'","&#39;")}
function money(x){return Number(x||0).toLocaleString('tr-TR',{minimumFractionDigits:2,maximumFractionDigits:2})+' TL'}

async function loadStores(){
  let d=await (await fetch('/stores')).json();
  let ss=$('storeSelect');
  ss.innerHTML=(d.stores||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.name)}${x.platform==='hepsiburada'?' — Hepsiburada':''}</option>`).join('');
  let saved=localStorage.getItem('ayse_store_id');
  if(saved&&[...ss.options].some(o=>o.value===saved))ss.value=saved;
  updateStoreInfo();
  ss.onchange=()=>{
    localStorage.setItem('ayse_store_id',ss.value);
    updateStoreInfo();
    $('colors').innerHTML='';colors=[];row=0;$('cc').value=0;
    loadDash();
  };
}
function updateStoreInfo(){
  let o=$('storeSelect').selectedOptions[0];
  $('storeInfo').textContent=o?`Aktif mağaza: ${o.textContent}. Tüm veriler bu mağazaya göre gösterilir.`:'';
  let id=currentStore();
  $('brandLabel').textContent=id==='trendyol_store2'?'Aynur Tesettür':'Ayşe Tesettür';
}
function tab(id,b){
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('on'));
  $(id).classList.add('on');b.classList.add('on');
  if(id==='dash')loadDash();
  if(id==='products')loadProducts();
  if(id==='inventory')loadInventory();
  if(id==='orders')loadOrders();
  if(id==='finance')loadFinance('');
  if(id==='reports')refreshReports();
}

// ============ DASHBOARD ============
async function loadDash(){
  let d=await (await fetch('/api/dashboard?period='+$('period').value+'&store_id='+encodeURIComponent(currentStore()))).json(),c=d.cards;
  $('kpis').innerHTML=[
    ['Ürün',c.products],['Varyant',c.variants],['Stok',c.stock],['Düşük Stok',c.low_stock],['Sipariş',c.orders],
    ['Satış',money(c.sales)],['İade',money(c.returns)],['Net',money(c.net)],['İade %',c.return_rate+'%'],['İptal %',c.cancel_rate+'%']
  ].map(x=>`<div class=kpi>${x[0]}<b>${x[1]}</b></div>`).join('');
  $('top').innerHTML='<tr><th>Ürün</th><th>Adet</th><th>Ciro</th></tr>'+(d.top||[]).map(x=>`<tr><td>${esc(x.name)}</td><td>${x.qty}</td><td>${money(x.revenue)}</td></tr>`).join('');
}

// ============ CATEGORY ============
async function searchCat(){
  let q=$('cq').value.trim();$('cats').innerHTML='Aranıyor...';
  let d=await (await fetch('/category-search?q='+encodeURIComponent(q)+'&store_id='+encodeURIComponent(currentStore()))).json();
  $('cats').innerHTML=(d.categories||[]).map(x=>`<button class=cat onclick="selectCat(${x.id},'${String(x.name).replaceAll("'","\\'")}')"><b>${esc(x.name)}</b> — ${x.leaf?'EN ALT KATEGORİ':'ALT KATEGORİ VAR'}<br><span class=hint>${esc(x.pathText)}</span></button>`).join('');
}
async function selectCat(id,name){
  let d=await (await fetch('/category-select/'+id+'?store_id='+encodeURIComponent(currentStore()))).json();
  if(!d.ok){$('cm').textContent=d.message;return}
  $('cid').value=id;$('cname').value=name;$('selt').textContent=d.category.pathText;$('sel').style.display='block';
  available=d.colors||[];source=d.colorSource||{};
  $('colors').innerHTML='';colors=[];row=0;$('cc').value=0;addColor();
}
function addColor(){
  if(!available.length){alert('Önce leaf kategori seç.');return}
  if(colors.length>=10){alert('En fazla 10 renk.');return}
  let i=row++,box=document.createElement('div');box.className='color';
  box.innerHTML=`<div class=colorhead><b>Renk ${colors.length+1}</b><button type=button onclick="this.closest('.color').remove();renum()">Sil</button></div>
    <select class=cs name=color_${i}_name required><option value="">-- Renk seç --</option>${available.map(x=>`<option value="${esc(x.name)}" data-id="${x.id}">${esc(x.name)}</option>`).join('')}</select>
    <input type=hidden class=src name=color_${i}_source_id value="${source.attributeId||''}">
    <input type=hidden class=vid name=color_${i}_value_id>
    <input type=file class=files name=color_${i}_images accept=image/* multiple required>
    <div class=preview><div class=hint>Fotoğraf seçince burada görünür.</div></div>`;
  $('colors').appendChild(box);colors.push(box);$('cc').value=colors.length;
  let s=box.querySelector('.cs'),v=box.querySelector('.vid'),fi=box.querySelector('.files'),pr=box.querySelector('.preview');
  s.onchange=()=>{v.value=s.options[s.selectedIndex]?.dataset.id||''};
  fi.onchange=()=>{if(fi.files.length>5){alert('Maksimum 5 fotoğraf.');fi.value='';return}preview(fi,pr)};
}
function renum(){
  colors=[...document.querySelectorAll('.color')];$('cc').value=colors.length;
  colors.forEach((x,i)=>{
    let n=i+1;x.querySelector('.colorhead b').textContent='Renk '+n;
    x.querySelector('.cs').name='color_'+n+'_name';
    x.querySelector('.src').name='color_'+n+'_source_id';
    x.querySelector('.vid').name='color_'+n+'_value_id';
    x.querySelector('.files').name='color_'+n+'_images';
  });
}
function preview(input,p){
  p.innerHTML='';
  let fs=[...input.files];
  fs.forEach((f,i)=>{
    let d=document.createElement('div');d.className='thumb '+(i?'':'main');
    let im=document.createElement('img');im.src=URL.createObjectURL(f);
    let b=document.createElement('div');b.className='badge';b.textContent=i?'İlk yapmak için tıkla':'★ Ana fotoğraf';
    d.append(im,b);
    d.onclick=()=>{
      if(!i)return;
      let a=[...input.files],x=a.splice(i,1)[0];a.unshift(x);
      let dt=new DataTransfer();a.forEach(z=>dt.items.add(z));input.files=dt.files;
      preview(input,p);
    };
    p.appendChild(d);
  });
}
$('form').onsubmit=async e=>{
  e.preventDefault();
  if(!$('cid').value){$('um').style.display='block';$('um').className='msg err';$('um').textContent='Kategori seç.';return}
  let fd=new FormData(e.target);fd.set('store_id',currentStore());
  $('send').disabled=true;
  try{
    let r=await fetch('/prepare',{method:'POST',body:fd}),d=await r.json();
    if(!r.ok)throw Error(d.detail||JSON.stringify(d));
    $('um').style.display='block';$('um').className='msg';$('um').textContent=JSON.stringify(d,null,2);
    loadDash();
  }catch(x){
    $('um').style.display='block';$('um').className='msg err';$('um').textContent=x.message;
  }finally{$('send').disabled=false}
};

// ============ PRODUCTS ============
async function loadProducts(){
  let d=await (await fetch('/api/products?q='+encodeURIComponent($('pq').value)+'&store_id='+encodeURIComponent(currentStore()))).json();
  $('pt').innerHTML=(d.items||[]).map(x=>`<tr><td>${esc(x.product_main_id)}</td><td>${esc(x.barcode)}</td><td>${esc(x.title)}</td><td>${x.quantity}</td><td>${money(x.sale_price)}</td></tr>`).join('');
}

// ============ INVENTORY ============
async function loadInventory(){
  let q=$('iq').value.trim();
  let r=await fetch('/api/products?q='+encodeURIComponent(q)+'&store_id='+encodeURIComponent(currentStore()));
  let d=await r.json();
  invRows=d.items||[];
  $('invTable').innerHTML=invRows.map((x,i)=>`
    <tr>
      <td><input type="checkbox" class="invChk" data-i="${i}"></td>
      <td>${esc(x.product_main_id)}</td>
      <td>${esc(x.barcode)}</td>
      <td>${esc(x.title)}</td>
      <td><input type="number" class="inv-qty invQty" data-i="${i}" value="${x.quantity}"></td>
      <td><input type="number" step="0.01" class="inv-price invPrice" data-i="${i}" value="${x.sale_price}"></td>
      <td>${esc(x.status||'')}</td>
      <td><button class="primary" onclick="saveOne(${i})">Kaydet</button></td>
    </tr>
  `).join('')||'<tr><td colspan="8">Kayıt yok.</td></tr>';
}
function toggleAllInv(cb){document.querySelectorAll('.invChk').forEach(x=>x.checked=cb.checked)}
function clearSelection(){document.querySelectorAll('.invChk').forEach(x=>x.checked=false);$('invAll').checked=false}
function selectedIndexes(){return [...document.querySelectorAll('.invChk:checked')].map(x=>Number(x.dataset.i))}
function bulkApply(field){
  const v=$('bulkQty').value;
  if(v===''){alert('Değer gir.');return}
  const idxs=selectedIndexes();
  if(!idxs.length){alert('Seçim yap.');return}
  idxs.forEach(i=>{
    if(field==='quantity')document.querySelector(`.invQty[data-i="${i}"]`).value=v;
    if(field==='salePrice')document.querySelector(`.invPrice[data-i="${i}"]`).value=v;
  });
}
async function saveOne(i){
  const row=invRows[i];
  const qty=document.querySelector(`.invQty[data-i="${i}"]`).value;
  const price=document.querySelector(`.invPrice[data-i="${i}"]`).value;
  const body={store_id:currentStore(),barcode:row.barcode,
    quantity:qty===''?null:Number(qty),
    salePrice:price===''?null:Number(price),
    listPrice:price===''?null:Number(price)};
  showInvMsg('Kaydediliyor...',false);
  try{
    const r=await fetch('/api/inventory/update-single',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||JSON.stringify(d));
    showInvMsg('✅ '+d.message,false);
  }catch(e){showInvMsg('❌ '+e.message,true)}
}
async function saveSelected(){
  const idxs=selectedIndexes();
  if(!idxs.length){alert('Seçim yap.');return}
  const items=idxs.map(i=>{
    const row=invRows[i];
    const qty=document.querySelector(`.invQty[data-i="${i}"]`).value;
    const price=document.querySelector(`.invPrice[data-i="${i}"]`).value;
    return {barcode:row.barcode,
      quantity:qty===''?null:Number(qty),
      salePrice:price===''?null:Number(price),
      listPrice:price===''?null:Number(price)};
  });
  showInvMsg(`Kaydediliyor ${items.length}...`,false);
  try{
    const r=await fetch('/api/inventory/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({store_id:currentStore(),items})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||JSON.stringify(d));
    showInvMsg('✅ '+d.message,false);loadInventory();
  }catch(e){showInvMsg('❌ '+e.message,true)}
}
function showInvMsg(txt,isErr){
  const el=$('invMsg');el.style.display='block';el.className='msg'+(isErr?' err':'');el.textContent=txt;
}
async function bulkAdjust(){
  const filter={};
  if($('bModel').value.trim())filter.product_main_id=$('bModel').value.trim();
  if($('bBarcode').value.trim())filter.barcode_contains=$('bBarcode').value.trim();
  if($('bMaxQ').value!=='')filter.max_quantity=Number($('bMaxQ').value);
  if($('bMinQ').value!=='')filter.min_quantity=Number($('bMinQ').value);
  const op={};
  if($('bSetQ').value!=='')op.set_quantity=Number($('bSetQ').value);
  if($('bAddQ').value!=='')op.add_quantity=Number($('bAddQ').value);
  if($('bSetP').value!=='')op.set_salePrice=Number($('bSetP').value);
  if($('bMulP').value!=='')op.multiply_price=Number($('bMulP').value);
  if(!Object.keys(op).length){alert('En az bir işlem gir.');return}
  $('bulkMsg').style.display='block';$('bulkMsg').className='msg';$('bulkMsg').textContent='İşleniyor...';
  try{
    const r=await fetch('/api/inventory/bulk-adjust',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({store_id:currentStore(),filter,operation:op})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||JSON.stringify(d));
    $('bulkMsg').textContent='✅ '+d.message+' (filtrelenen: '+d.filteredCount+')';
    loadInventory();
  }catch(e){
    $('bulkMsg').className='msg err';$('bulkMsg').textContent='❌ '+e.message;
  }
}

// ============ ORDERS ============
async function loadOrders(){
  let d=await (await fetch('/api/orders?q='+encodeURIComponent($('oq').value)+'&store_id='+encodeURIComponent(currentStore()))).json();
  $('ot').innerHTML=(d.items||[]).map(x=>`<tr><td>${new Date((x.order_date||0)).toLocaleString('tr-TR')}</td><td>${esc(x.order_number)}</td><td>${esc(x.shipment_package_id)}</td><td>${esc(x.status)}</td><td>${money(x.total_price)}</td></tr>`).join('');
}

// ============ FINANCE ============
async function loadFinance(t){
  let d=await (await fetch('/api/finance?t='+t+'&store_id='+encodeURIComponent(currentStore()))).json();
  $('ft').innerHTML=(d.items||[]).map(x=>`<tr><td>${new Date((x.transaction_date||0)).toLocaleString('tr-TR')}</td><td>${esc(x.transaction_type)}</td><td>${esc(x.order_number)}</td><td>${esc(x.barcode)}</td><td>${money(x.amount)}</td></tr>`).join('');
}

// ============ REPORTS ============
async function refreshReports(){
  await Promise.all([loadSummary(),loadDailySales(),loadTopProducts(),loadLowStock()]);
}
async function loadSummary(){
  const p=$('rPeriod').value;
  const r=await fetch(`/api/reports/store-summary?period=${p}&store_id=${encodeURIComponent(currentStore())}`);
  const d=await r.json();
  $('rSummary').innerHTML=[
    ['Satış',money(d.sales.amount)],['Net',money(d.net)],['İade',money(d.returns.amount)],
    ['Sipariş',d.orders.count],['İptal',d.orders.cancelled],['İade %',d.return_rate+'%'],
    ['İptal %',d.cancel_rate+'%'],['Stok Birim',d.stock.total_units],
    ['Ürün',d.stock.product_count],['Satış Sayısı',d.sales.count]
  ].map(x=>`<div class="kpi">${x[0]}<b>${x[1]}</b></div>`).join('');
}
async function loadDailySales(){
  const p=$('rPeriod').value;
  const r=await fetch(`/api/reports/sales-by-day?period=${p}&store_id=${encodeURIComponent(currentStore())}`);
  const d=await r.json();
  $('rDaily').innerHTML=(d.items||[]).map(x=>`<tr><td>${esc(x.d)}</td><td>${money(x.sales)}</td><td>${money(x.returns)}</td><td><b>${money(x.sales-x.returns)}</b></td><td>${x.sale_count}</td><td>${x.return_count}</td></tr>`).join('')||'<tr><td colspan="6">Veri yok.</td></tr>';
}
async function loadTopProducts(){
  const p=$('rPeriod').value;
  const r=await fetch(`/api/reports/top-products?period=${p}&limit=50&store_id=${encodeURIComponent(currentStore())}`);
  const d=await r.json();
  $('rTop').innerHTML=(d.items||[]).map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.name)}</td><td>${esc(x.barcode)}</td><td>${x.qty}</td><td>${money(x.revenue)}</td><td>${x.orders}</td></tr>`).join('')||'<tr><td colspan="6">Veri yok.</td></tr>';
}
async function loadLowStock(){
  const t=Number($('rThreshold').value)||5;
  const r=await fetch(`/api/reports/low-stock?threshold=${t}&store_id=${encodeURIComponent(currentStore())}`);
  const d=await r.json();
  $('rLow').innerHTML=(d.items||[]).map(x=>`<tr><td>${esc(x.product_main_id)}</td><td>${esc(x.barcode)}</td><td>${esc(x.title)}</td><td style="color:${x.quantity<=2?'#b42318':'#b45309'}"><b>${x.quantity}</b></td><td>${money(x.sale_price)}</td></tr>`).join('')||'<tr><td colspan="5">Veri yok.</td></tr>';
}
function exportCSV(kind){
  const p=$('rPeriod').value;
  window.open(`/api/reports/export-csv?kind=${kind}&period=${p}&store_id=${encodeURIComponent(currentStore())}`,'_blank');
}

// ============ SYNC ============
async function sync(t){
  $('sm').style.display='block';$('sm').className='msg';$('sm').textContent='Senkronizasyon başladı...';
  try{
    const url='/sync/'+t+(t==='orders'?'?days=14&store_id=':t==='finance'||t==='all'?'?days=30&store_id=':'?store_id=')+encodeURIComponent(currentStore());
    const r=await fetch(url,{method:'POST'});
    const d=await r.json();
    if(!r.ok)throw Error(d.detail||JSON.stringify(d));
    $('sm').textContent=JSON.stringify(d,null,2);loadDash();
  }catch(e){$('sm').className='msg err';$('sm').textContent=e.message}
}

loadStores().then(()=>loadDash());
</script></body></html>'''

@app.get('/',response_class=HTMLResponse)
def home():return HTMLResponse(PAGE)