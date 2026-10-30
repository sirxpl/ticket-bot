"""Ticket analytics from saved event metadata; never reads message content."""
import datetime as dt
import math
import time
from collections import Counter, defaultdict
from urllib.parse import urlparse

UTC = dt.timezone.utc


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            result = parsed.replace(tzinfo=UTC).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def duration(value):
    if value is None:
        return 'No data'
    value = max(0, round(value))
    if value < 60:
        return f'{value}s'
    if value < 3600:
        return f'{value / 60:.1f}m'
    if value < 86400:
        return f'{value / 3600:.1f}h'
    return f'{value / 86400:.1f}d'


def avatar_url(user_id, value=None):
    """Only embed Discord CDN URLs, including guild-specific avatars."""
    parsed = urlparse(str(value or ''))
    if parsed.scheme == 'https' and parsed.hostname in {'cdn.discordapp.com', 'media.discordapp.net'}:
        return str(value)
    uid = str(user_id or '')
    index = (int(uid) >> 22) % 6 if uid.isdigit() else 0
    return f'https://cdn.discordapp.com/embed/avatars/{index}.png'


def summary(values):
    values = sorted(values)
    return {
        'count': len(values),
        'average': sum(values) / len(values) if values else None,
        'median': (values[(len(values)-1)//2] + values[len(values)//2]) / 2 if values else None,
        'p90': values[math.ceil(len(values) * .9)-1] if values else None,
    }


def build_analytics(logs, active_tickets, days=7, category='', now=None):
    days = max(1, min(int(days or 7), 90))
    now = time.time() if now is None else now
    today = dt.datetime.fromtimestamp(now, UTC).date()
    start = dt.datetime.combine(today - dt.timedelta(days=days-1), dt.time.min, UTC).timestamp()
    previous_start = start - days * 86400
    events = []
    for entry in logs:
        stamp = timestamp(entry.get('timestamp'))
        if stamp is not None and stamp <= now and str(entry.get('ticket_id') or '') not in {'', 'global'}:
            events.append({**entry, '_ts': stamp, 'ticket_id': str(entry['ticket_id'])})
    events.sort(key=lambda entry: entry['_ts'])
    seen_messages = set()
    unique = []
    for entry in events:
        message_id = entry.get('message_id')
        if message_id and entry.get('action') in {'staff_message','staff_response','member_message'}:
            key = (entry['ticket_id'], str(message_id))
            if key in seen_messages: continue
            seen_messages.add(key)
        unique.append(entry)
    events = unique
    created, closed, claimed, replied, member_posts = {}, {}, {}, {}, set()
    for entry in events:
        tid, action = entry['ticket_id'], entry.get('action')
        if action == 'created': created.setdefault(tid, entry)
        elif action == 'closed': closed.setdefault(tid, entry)
        elif action in {'ticket_started', 'claimed'}: claimed.setdefault(tid, entry)
        elif action in {'staff_message', 'staff_response'}: replied.setdefault(tid, entry)
        elif action == 'member_message': member_posts.add(tid)
    def panel(tid):
        return str(created.get(tid, {}).get('category_label') or 'Uncategorized')
    categories = sorted({panel(tid) for tid in created} | {str(t.get('category_label') or 'Uncategorized') for t in active_tickets})
    if category not in categories:
        category = ''
    # A malformed pre-creation event cannot hide a later valid first response.
    for mapping, actions in [(claimed, {'ticket_started','claimed'}), (replied, {'staff_message','staff_response'})]:
        mapping.clear()
        for entry in events:
            tid = entry['ticket_id']
            if entry.get('action') in actions and entry['_ts'] >= created.get(tid, {}).get('_ts', 0):
                mapping.setdefault(tid, entry)
    accepted = {tid for tid in created if not category or panel(tid) == category}
    # Legacy close-only events still count in unfiltered totals.
    if not category: accepted.update(entry['ticket_id'] for entry in events)
    events = [entry for entry in events if entry['ticket_id'] in accepted]
    created = {k:v for k,v in created.items() if k in accepted}
    closed = {k:v for k,v in closed.items() if k in accepted}
    claimed = {k:v for k,v in claimed.items() if k in accepted}
    replied = {k:v for k,v in replied.items() if k in accepted}
    def in_period(entry): return start <= entry['_ts'] <= now
    period_created = {k:v for k,v in created.items() if in_period(v)}
    period_closed = {k:v for k,v in closed.items() if in_period(v)}
    active = [t for t in active_tickets if not category or str(t.get('category_label') or 'Uncategorized') == category]
    ages = [now-stamp for t in active if (stamp := timestamp(t.get('created_at'))) is not None and stamp <= now]
    queue_age = [
        {'label': label, 'value': sum(low <= age < high for age in ages)}
        for label, low, high in [('Under 1h',0,3600),('1h to 1d',3600,86400),('1d to 3d',86400,259200),('Over 3d',259200,float('inf'))]
    ]
    metrics = {'reply': [], 'claim': [], 'close': []}
    previous = {key: [] for key in metrics}
    daily = {}
    for offset in range(days):
        date = today - dt.timedelta(days=days-1-offset)
        daily[date.isoformat()] = {'date':date.strftime('%b %d'), 'iso':date.isoformat(), 'created':0, 'closed':0, 'reply':[], 'claim':[], 'close':[]}
    for key, mapping in [('created',created),('closed',closed)]:
        for entry in mapping.values():
            date = dt.datetime.fromtimestamp(entry['_ts'],UTC).date().isoformat()
            if date in daily: daily[date][key] += 1
    for kind, mapping in [('reply',replied),('claim',claimed),('close',closed)]:
        for tid, entry in mapping.items():
            origin = created.get(tid)
            if not origin: continue
            delay = entry['_ts'] - origin['_ts']
            if delay < 0: continue
            if in_period(entry):
                metrics[kind].append(delay)
                daily[dt.datetime.fromtimestamp(entry['_ts'],UTC).date().isoformat()][kind].append(delay)
            elif previous_start <= entry['_ts'] < start:
                previous[kind].append(delay)
    summaries = {key: summary(values) for key,values in metrics.items()}
    for row in daily.values():
        for key in metrics:
            row[key] = summary(row[key])['average']
    previous_flow = sum(previous_start <= entry['_ts'] < start for entry in created.values()) / days
    baselines = {key: summary(values)['average'] for key,values in previous.items()}
    baselines['flow'] = previous_flow
    panels = []
    for name, count in Counter(panel(tid) for tid in period_created).most_common():
        ids = [tid for tid in period_created if panel(tid) == name]
        panels.append({'label':name, 'value':count, 'open':sum(str(t.get('channel_id')) in ids for t in active), 'claimed':sum(tid in claimed for tid in ids)})
    cohort_count = len(period_created)
    lifecycle = [{'label':label, 'value':count, 'percent':round(count/cohort_count*100) if cohort_count else 0} for label,count in [
        ('Opened',cohort_count),('Member wrote something',sum(t in member_posts for t in period_created)),
        ('Staff replied',sum(t in replied for t in period_created)),('Work started',sum(t in claimed for t in period_created)),('Closed',sum(t in closed for t in period_created))]]
    reasons = Counter(str(e.get('reason') or 'No reason provided') for e in period_closed.values())
    endings = Counter()
    rows = {}
    for entry in events:
        if not in_period(entry): continue
        action = entry.get('action')
        actor = entry.get('actor') or entry.get('executor') or {}
        uid = str(actor.get('id') or '')
        tid = entry['ticket_id']
        owner = str((created.get(tid,{}).get('creator') or {}).get('id') or entry.get('allowed_user_id') or '')
        if action == 'closed' and period_closed.get(tid) is entry:
            endings[entry.get('close_kind') or ('Closed by opener' if uid and uid == owner else 'Other / legacy close')] += 1
        if action not in {'closed','staff_message','staff_response'} or not uid or uid == owner or actor.get('bot'): continue
        row = rows.setdefault(uid, {'id':uid,'name':actor.get('name') or 'Unknown staff','avatar_url':avatar_url(uid),'closed':0,'responses':0,'reply_times':[]})
        row['name'] = actor.get('name') or row['name']
        row['avatar_url'] = avatar_url(uid, actor.get('avatar_url') or row['avatar_url'])
        if action == 'closed' and period_closed.get(tid) is entry: row['closed'] += 1
        elif action in {'staff_message','staff_response'}:
            row['responses'] += 1
            if replied.get(tid) is entry and tid in created and entry['_ts'] >= created[tid]['_ts']:
                row['reply_times'].append(entry['_ts']-created[tid]['_ts'])
    # events were copied once; identities are shared with the first-event indexes above.
    for row in rows.values():
        row['avg_reply'] = summary(row.pop('reply_times'))['average']
        row['activity'] = row['closed'] + row['responses']
    board = sorted(rows.values(), key=lambda r:(-r['closed'],-r['responses'],r['name'].lower()))[:5]
    heat = [[0 for _ in range(24)] for _ in range(7)]
    for entry in period_created.values():
        date = dt.datetime.fromtimestamp(entry['_ts'],UTC)
        heat[date.weekday()][date.hour] += 1
    spreads = {}
    bounds = [0,60,300,900,3600,21600,86400,float('inf')]
    labels = ['<1m','1–5m','5–15m','15–60m','1–6h','6–24h','1d+']
    for key,values in metrics.items():
        spreads[key] = [{'label':labels[i], 'value':sum(bounds[i] <= v < bounds[i+1] for v in values)} for i in range(len(labels))]
    return {
        'all_time_created':len(created),'all_time_closed':len(closed),'all_time_claimed':len(claimed),
        'last_24h_created':sum(e['_ts']>=now-86400 for e in created.values()),
        'last_24h_closed':sum(e['_ts']>=now-86400 for e in closed.values()),
        'period_created':len(period_created),'period_closed':len(period_closed),
        'open_now':len(active),'waiting_on_staff':sum(str(t.get('channel_id')) not in replied for t in active),
        'unclaimed':sum(str(t.get('channel_id')) not in claimed for t in active),
        'oldest_open':max(ages) if ages else None,'queue_unknown_age':len(active)-len(ages),
        'daily':list(daily.values()),'baselines':baselines,'metrics':summaries,'spreads':spreads,
        'panels':panels,'lifecycle':lifecycle,'queue_age':queue_age,
        'close_reasons':[{'label':k,'value':v} for k,v in reasons.most_common(8)],
        'endings':[{'label':k,'value':v} for k,v in endings.most_common()],
        'heatmap':heat,'heatmap_max':max((max(day) for day in heat),default=0),
        'staff_leaderboard':board,'categories':categories,'selected_category':category,
        'avg_response_seconds':summaries['reply']['average'],'response_count':summaries['reply']['count'],
    }
