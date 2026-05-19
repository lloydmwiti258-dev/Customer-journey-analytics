import os
import json
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build
import pandas as pd
from dateutil import parser as dateparser

app = Flask(__name__)
CORS(app)

# ── CONFIG ──────────────────────────────────────────────────────────────────
JSON_FILE_PATH = r'C:\Users\Administrator\Downloads\retention-485013-974e48474123.json'
SPREADSHEET_ID = '1zravAS7NoxjnV-2476eBhMitZYQmxWgef3JTbwD-Rag'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

REGION_MAP = {
    'Hazina': 'Nairobi CBD', 'Hilton': 'Nairobi CBD',
    'Starmall': 'Nairobi CBD', 'Ktda': 'Nairobi CBD',
    'Mombasa': 'Coastal Region',
    'Kakamega': 'Western & Nyanza', 'Kisumu': 'Western & Nyanza',
    'Kisii': 'Western & Nyanza', 'Busia': 'Western & Nyanza',
    'Meru': 'Central Region', 'Nanyuki': 'Central Region',
    'Thika': 'Central Region',
    'Eldoret': 'Rift Valley', 'Nakuru': 'Rift Valley',
    'Kitengela': 'Rift Valley', 'Rongai': 'Rift Valley',
    'Sinza': 'Diaspora', 'Tanzania': 'Diaspora', 'Uganda': 'Diaspora',
    'Website': 'Online', 'Rejects': 'Reject'
}

META_SOURCES = {
    'facebook', 'meta ad', 'meta ads', 'direct ig', 'instagram',
    'ig', 'liz', 'meta ad x', 'meta ad fb', 'meta ad-ig', 'new direct'
}
TIKTOK_SOURCES = {'tik tok', 'tiktok'}
NO_SPEND_SOURCES = {'e direct', 'web check out', 'website', 'existing'}


def normalize_source(src):
    if not src:
        return ''
    return str(src).strip().lower()


def classify_source(src):
    s = normalize_source(src)
    if any(k in s for k in TIKTOK_SOURCES):
        return 'tiktok'
    for k in META_SOURCES:
        if k in s:
            return 'meta'
    if any(k in s for k in NO_SPEND_SOURCES):
        return 'organic'
    return 'other'


def normalize_phone(p):
    if not p:
        return ''
    p = re.sub(r'[\s\-\(\)\+]', '', str(p))
    if p.startswith('254') and len(p) >= 12:
        return '0' + p[3:]
    if p.startswith('7') and len(p) == 9:
        return '0' + p
    return p


def safe_date(val):
    try:
        return dateparser.parse(str(val), dayfirst=True)
    except Exception:
        return None


def get_sheet_data(service, range_name):
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name
    ).execute()
    rows = result.get('values', [])
    if not rows:
        return pd.DataFrame()
    headers = rows[0]
    data = rows[1:]
    # Pad short rows
    max_cols = len(headers)
    padded = [r + [''] * (max_cols - len(r)) for r in data]
    return pd.DataFrame(padded, columns=headers)


def load_data():
    creds = service_account.Credentials.from_service_account_file(
        JSON_FILE_PATH, scopes=SCOPES)
    service = build('sheets', 'v4', credentials=creds)

    # Sheet 1: Shops (dynamic — find all shop sheets)
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_names = [s['properties']['title'] for s in meta['sheets']]

    shop_frames = []
    for name in sheet_names:
        low = name.strip().lower()
        if low in ('leads_2025', 'whatsapp', 'sheet2', 'sheet3'):
            continue
        try:
            df = get_sheet_data(service, f"'{name}'!A:K")
            if df.empty:
                continue
            # Rename columns positionally to standard names
            col_map = {
                0: 'date', 1: 'first_name', 2: 'gender', 3: 'phone',
                4: 'product', 5: 'color', 6: 'category', 7: 'location',
                8: 'price', 9: 'meta_spend', 10: 'tiktok_spend'
            }
            df.columns = [col_map.get(i, df.columns[i])
                          for i in range(len(df.columns))]
            df['shop'] = name
            df['region'] = REGION_MAP.get(name, 'Other')
            shop_frames.append(df)
        except Exception:
            pass

    shops_df = pd.concat(shop_frames, ignore_index=True) if shop_frames else pd.DataFrame()

    # Sheet 2: Leads_2025
    leads_df = pd.DataFrame()
    for n in sheet_names:
        if 'lead' in n.lower():
            try:
                leads_df = get_sheet_data(service, f"'{n}'!A:E")
                leads_df.columns = ['date', 'contact', 'name', 'branch', 'source'][:len(leads_df.columns)]
            except Exception:
                pass

    # Sheet 3: Whatsapp
    wa_df = pd.DataFrame()
    for n in sheet_names:
        if 'whatsapp' in n.lower() or 'whats' in n.lower():
            try:
                wa_df = get_sheet_data(service, f"'{n}'!A:F")
                wa_df.columns = ['date', 'name', 'contact', 'source', 'activity', 'branch'][:len(wa_df.columns)]
            except Exception:
                pass

    return shops_df, leads_df, wa_df


def compute_analytics(shops_df, leads_df, wa_df):
    now = datetime.now()
    results = {}

    # ── Parse dates ──────────────────────────────────────────────────────────
    if not leads_df.empty:
        leads_df = leads_df.copy()
        leads_df['date_parsed'] = leads_df['date'].apply(safe_date)
        leads_df['phone_norm'] = leads_df['contact'].apply(normalize_phone)
        leads_df['source_class'] = leads_df['source'].apply(classify_source)

    if not wa_df.empty:
        wa_df = wa_df.copy()
        wa_df['date_parsed'] = wa_df['date'].apply(safe_date)
        wa_df['phone_norm'] = wa_df['contact'].apply(normalize_phone)
        wa_df['source_class'] = wa_df['source'].apply(classify_source)

    if not shops_df.empty:
        shops_df = shops_df.copy()
        shops_df['date_parsed'] = shops_df['date'].apply(safe_date)
        shops_df['phone_norm'] = shops_df['phone'].apply(normalize_phone)
        try:
            shops_df['price_num'] = pd.to_numeric(
                shops_df['price'].str.replace(',', ''), errors='coerce').fillna(0)
            shops_df['meta_spend_num'] = pd.to_numeric(
                shops_df['meta_spend'].str.replace(',', ''), errors='coerce').fillna(0)
            shops_df['tiktok_spend_num'] = pd.to_numeric(
                shops_df['tiktok_spend'].str.replace(',', ''), errors='coerce').fillna(0)
        except Exception:
            shops_df['price_num'] = 0
            shops_df['meta_spend_num'] = 0
            shops_df['tiktok_spend_num'] = 0

    # ── 1. Total Leads ────────────────────────────────────────────────────────
    total_leads = len(leads_df) if not leads_df.empty else 0
    wa_leads = len(wa_df) if not wa_df.empty else 0
    results['total_leads'] = total_leads
    results['total_wa_engagements'] = wa_leads

    # ── 2. Source Lead Generation ─────────────────────────────────────────────
    if not leads_df.empty and 'source' in leads_df.columns:
        src_counts = leads_df['source'].str.strip().str.lower().value_counts().to_dict()
        results['source_breakdown'] = src_counts

        class_counts = leads_df['source_class'].value_counts().to_dict()
        results['source_class_breakdown'] = class_counts
    else:
        results['source_breakdown'] = {}
        results['source_class_breakdown'] = {}

    # ── 3. Branch Performance: Leads vs Conversions ───────────────────────────
    branch_leads = {}
    if not leads_df.empty and 'branch' in leads_df.columns:
        branch_leads = leads_df['branch'].str.strip().value_counts().to_dict()

    branch_conv = {}
    region_conv = {}
    if not shops_df.empty and 'shop' in shops_df.columns:
        branch_conv = shops_df['shop'].value_counts().to_dict()
        region_conv = shops_df['region'].value_counts().to_dict()

    all_branches = set(list(branch_leads.keys()) + list(branch_conv.keys()))
    branch_perf = []
    for b in sorted(all_branches):
        leads_n = branch_leads.get(b, 0)
        conv_n = branch_conv.get(b, 0)
        rate = round(conv_n / leads_n * 100, 1) if leads_n > 0 else 0
        branch_perf.append({
            'branch': b,
            'leads': leads_n,
            'conversions': conv_n,
            'rate': rate,
            'region': REGION_MAP.get(b, 'Other')
        })
    results['branch_performance'] = branch_perf
    results['region_conversions'] = region_conv

    # ── 4. Customer Journey: Lead → Conversion Time ───────────────────────────
    journey_times = []
    matched_journeys = []
    if not leads_df.empty and not shops_df.empty:
        for _, lead in leads_df.iterrows():
            phone = lead.get('phone_norm', '')
            if not phone:
                continue
            lead_date = lead.get('date_parsed')
            if not lead_date:
                continue
            conv_rows = shops_df[shops_df['phone_norm'] == phone]
            for _, conv in conv_rows.iterrows():
                conv_date = conv.get('date_parsed')
                if conv_date and conv_date >= lead_date:
                    delta = (conv_date - lead_date).days
                    journey_times.append(delta)
                    matched_journeys.append({
                        'name': lead.get('name', ''),
                        'phone': phone,
                        'lead_date': str(lead_date.date()),
                        'conv_date': str(conv_date.date()),
                        'days_to_convert': delta,
                        'shop': conv.get('shop', ''),
                        'source': lead.get('source', '')
                    })

    if journey_times:
        results['avg_journey_days'] = round(sum(journey_times) / len(journey_times), 1)
        results['min_journey_days'] = min(journey_times)
        results['max_journey_days'] = max(journey_times)
        results['journey_distribution'] = {
            'same_day': sum(1 for d in journey_times if d == 0),
            '1_7_days': sum(1 for d in journey_times if 1 <= d <= 7),
            '8_30_days': sum(1 for d in journey_times if 8 <= d <= 30),
            '31_90_days': sum(1 for d in journey_times if 31 <= d <= 90),
            '90_plus': sum(1 for d in journey_times if d > 90),
        }
    else:
        results['avg_journey_days'] = None
        results['journey_distribution'] = {}
    results['matched_journeys'] = matched_journeys[:50]  # top 50 for table

    # ── 5. Unique Leads Engaged ───────────────────────────────────────────────
    all_phones = set()
    if not leads_df.empty and 'phone_norm' in leads_df.columns:
        all_phones.update(leads_df['phone_norm'].dropna().unique())
    if not wa_df.empty and 'phone_norm' in wa_df.columns:
        all_phones.update(wa_df['phone_norm'].dropna().unique())
    all_phones.discard('')
    results['unique_leads'] = len(all_phones)

    # ── 6. Lead Matching & Conversion Rate ───────────────────────────────────
    converted_phones = set()
    if not shops_df.empty and 'phone_norm' in shops_df.columns:
        converted_phones = set(shops_df['phone_norm'].dropna().unique())
        converted_phones.discard('')

    lead_phones = set()
    if not leads_df.empty and 'phone_norm' in leads_df.columns:
        lead_phones = set(leads_df['phone_norm'].dropna().unique())
        lead_phones.discard('')

    matched_converted = lead_phones & converted_phones
    results['leads_converted'] = len(matched_converted)
    results['total_conversions'] = len(shops_df) if not shops_df.empty else 0
    results['conversion_rate'] = round(
        len(matched_converted) / len(lead_phones) * 100, 2
    ) if lead_phones else 0

    # ── 7. Lead Status ────────────────────────────────────────────────────────
    hot, warm, cold = [], [], []
    if not leads_df.empty and 'phone_norm' in leads_df.columns:
        for _, row in leads_df.iterrows():
            phone = row.get('phone_norm', '')
            d = row.get('date_parsed')
            if not d:
                continue
            days_since = (now - d).days if d else 9999
            if phone in converted_phones:
                continue  # already converted
            if days_since <= 30:
                hot.append({'name': row.get('name', ''), 'phone': phone,
                            'source': row.get('source', ''), 'days': days_since,
                            'branch': row.get('branch', '')})
            elif days_since <= 90:
                warm.append({'name': row.get('name', ''), 'phone': phone,
                             'source': row.get('source', ''), 'days': days_since,
                             'branch': row.get('branch', '')})
            else:
                cold.append({'name': row.get('name', ''), 'phone': phone,
                             'source': row.get('source', ''), 'days': days_since,
                             'branch': row.get('branch', '')})

    results['hot_leads'] = {'count': len(hot), 'items': hot[:30]}
    results['warm_leads'] = {'count': len(warm), 'items': warm[:30]}
    results['cold_leads'] = {'count': len(cold), 'items': cold[:30]}

    # ── 8. Marketing Source Metrics & ROI ────────────────────────────────────
    # Spend from shops sheet
    meta_spend_total = 0
    tiktok_spend_total = 0
    if not shops_df.empty:
        meta_spend_total = shops_df['meta_spend_num'].sum()
        tiktok_spend_total = shops_df['tiktok_spend_num'].sum()

    # Revenue per source class
    revenue_by_class = {'meta': 0, 'tiktok': 0, 'organic': 0, 'other': 0}
    leads_by_class = {'meta': 0, 'tiktok': 0, 'organic': 0, 'other': 0}
    conv_by_class = {'meta': 0, 'tiktok': 0, 'organic': 0, 'other': 0}

    if not leads_df.empty and 'source_class' in leads_df.columns:
        for cls, grp in leads_df.groupby('source_class'):
            if cls in leads_by_class:
                leads_by_class[cls] = len(grp)
                phones = set(grp['phone_norm'].dropna().unique())
                conv = phones & converted_phones
                conv_by_class[cls] = len(conv)
                # Revenue from matched conversions
                if not shops_df.empty:
                    rev = shops_df[shops_df['phone_norm'].isin(conv)]['price_num'].sum()
                    revenue_by_class[cls] = float(rev)

    spend = {'meta': float(meta_spend_total), 'tiktok': float(tiktok_spend_total),
             'organic': 0, 'other': 0}

    source_roi = {}
    for cls in ['meta', 'tiktok', 'organic', 'other']:
        s = spend[cls]
        rev = revenue_by_class[cls]
        roi = round((rev - s) / s * 100, 1) if s > 0 else None
        cpl = round(s / leads_by_class[cls], 2) if leads_by_class[cls] > 0 and s > 0 else None
        source_roi[cls] = {
            'leads': leads_by_class[cls],
            'conversions': conv_by_class[cls],
            'spend': s,
            'revenue': rev,
            'roi': roi,
            'cpl': cpl,
            'conv_rate': round(conv_by_class[cls] / leads_by_class[cls] * 100, 1)
                if leads_by_class[cls] > 0 else 0
        }
    results['source_roi'] = source_roi

    # Raw source breakdown with activity
    if not wa_df.empty and 'source' in wa_df.columns:
        wa_src = wa_df.groupby('source').agg(
            count=('source', 'count'),
            activity_sample=('activity', lambda x: x.mode()[0] if len(x) > 0 else '')
        ).reset_index().to_dict('records')
        results['wa_source_activity'] = wa_src
    else:
        results['wa_source_activity'] = []

    # Monthly trends
    if not shops_df.empty and 'date_parsed' in shops_df.columns:
        shops_df['month'] = shops_df['date_parsed'].apply(
            lambda d: d.strftime('%Y-%m') if pd.notnull(d) and d else None)
        monthly = shops_df.dropna(subset=['month']).groupby('month').agg(
            conversions=('shop', 'count'),
            revenue=('price_num', 'sum')
        ).reset_index().to_dict('records')
        results['monthly_conversions'] = monthly
    else:
        results['monthly_conversions'] = []

    # Top products
    if not shops_df.empty and 'product' in shops_df.columns:
        prod = shops_df['product'].str.strip().value_counts().head(10).to_dict()
        results['top_products'] = prod
    else:
        results['top_products'] = {}

    # Gender split
    if not shops_df.empty and 'gender' in shops_df.columns:
        results['gender_split'] = shops_df['gender'].str.strip().str.title().value_counts().to_dict()
    else:
        results['gender_split'] = {}

    return results


# ── Cache ────────────────────────────────────────────────────────────────────
_cache = {'data': None, 'ts': None}


def get_analytics(force=False):
    global _cache
    if not force and _cache['data'] and _cache['ts']:
        age = (datetime.now() - _cache['ts']).seconds
        if age < 300:
            return _cache['data']
    shops_df, leads_df, wa_df = load_data()
    data = compute_analytics(shops_df, leads_df, wa_df)
    _cache = {'data': data, 'ts': datetime.now()}
    return data


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/api/analytics')
def api_analytics():
    try:
        data = get_analytics()
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/refresh')
def api_refresh():
    try:
        data = get_analytics(force=True)
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


HTML_TEMPLATE = open(os.path.join(os.path.dirname(__file__), 'index.html')).read()

if __name__ == '__main__':
    print("🚀 Customer Journey Analytics Dashboard")
    print("   http://localhost:5000")
    app.run(debug=True, port=5000)
