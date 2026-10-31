"""Watches official Saudi news for changes to the moon sighting or the school calendar, and opens an issue for the owner.

It only reads public headlines and never edits sighting.json or school.json: publishing waits for the owner.
Standard library only, so the workflow needs no installs.
"""
import html, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

OFFICIAL = ('spa.gov.sa', 'moe.gov.sa')
QUERIES = {
    'sighting': ['site:spa.gov.sa هلال when:3d', 'site:spa.gov.sa الديوان الملكي رؤية when:3d'],
    'school': ['site:spa.gov.sa وزارة التعليم إجازة when:3d', 'site:spa.gov.sa التقويم الدراسي when:3d',
               'site:moe.gov.sa التقويم الدراسي when:3d'],
}
LABELS = {'sighting': 'رؤية الهلال', 'school': 'التقويم الدراسي'}
OWNER = 'JJASOFTWAREE'
STATE = 'watch/seen.json'
STATUS = 'watch/status.json'
TRIAL = 'watch/trial.json'


COURT = ('المحكمة العليا', 'الديوان الملكي', 'مجلس القضاء')
RESULT = ('ثبوت', 'المكمل', 'غرة', 'أول أيام', 'يوم عيد', 'الوقوف بعرفة', 'يوم عرفة', 'تعذر', 'لم ير')
SUBJECT = ('إجازة', 'الإجازة', 'التقويم الدراسي', 'العام الدراسي', 'الفصل الدراسي', 'الدراسة', 'عودة الطلاب')
CHANGE = ('تمديد', 'تمدد', 'تقديم', 'تقدم', 'تبكير', 'تأجيل', 'تؤجل', 'تعديل', 'تعدل', 'إضافة', 'إضافي', 'اعتماد', 'تعتمد',
          'يعتمد', 'إقرار', 'تقر', 'يقر', 'يوجه', 'توجه', 'تعلن', 'موعد', 'يوافق', 'موافقة', 'الجديد', 'يشتمل')


def plain(text):
    """Arabic headlines carry harakat and tatweel unevenly; matching ignores both."""
    return re.sub('[ً-ْـ]', '', text)


def relevant(kind, title):
    """Headlines that can change the data: a court sighting result, or a school calendar or break decision.
    A call to look for the moon («تدعو إلى تحري») changes nothing yet."""
    t = plain(title)
    if kind == 'sighting':
        return 'تحري' not in t and (('هلال' in t and 'ثبوت' in t) or (any(w in t for w in COURT) and any(w in t for w in RESULT)))
    return any(w in t for w in SUBJECT) and any(w in t for w in CHANGE)


def fetch(query):
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': 'ar', 'gl': 'SA', 'ceid': 'SA:ar'})
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as r:
        body = r.read().decode('utf-8')
    items = []
    for raw in re.findall(r'<item>(.*?)</item>', body, re.S):
        def tag(name):
            m = re.search(rf'<{name}[^>]*>(.*?)</{name}>', raw, re.S)
            return html.unescape(m.group(1)).strip() if m else ''
        source = re.search(r'<source url="([^"]*)"', raw)
        host = (urllib.parse.urlparse(source.group(1)).hostname or '') if source else ''
        title = re.sub(r'\s+-\s+وكالة الأنباء السعودية\s*$', '', tag('title'))
        items.append({'id': tag('guid') or tag('link'), 'title': title, 'link': tag('link'), 'date': tag('pubDate'), 'host': host})
    return items


def official(host):
    return any(host == d or host.endswith('.' + d) for d in OFFICIAL)


def open_issue(item, kind, trial=False):
    title = ('[تجربة] ' if trial else '') + f'{LABELS[kind]}: {item["title"]}'[:240]
    body = '\n'.join([
        f'خبر رسمي جديد قد يغيّر بيانات «أيام» ({LABELS[kind]}).',
        '',
        f'- **الخبر:** {item["title"]}',
        f'- **المصدر:** {item["host"]}',
        f'- **التاريخ:** {item["date"]}',
        f'- **الرابط:** {item["link"]}',
        '',
        'لم يُنشر أي تعديل. إذا كان فيه تغيير على التواريخ، أرسل في Claude: «شوف إشعار المراقب».',
        '',
        f'@{OWNER}',
    ])
    request = urllib.request.Request(
        f'https://api.github.com/repos/{os.environ["REPO"]}/issues', method='POST',
        data=json.dumps({'title': title, 'body': body}).encode('utf-8'),
        headers={'Authorization': f'Bearer {os.environ["GH_TOKEN"]}', 'Accept': 'application/vnd.github+json',
                 'User-Agent': 'hijri-data-watch'})
    with urllib.request.urlopen(request, timeout=30) as r:
        return json.load(r)['html_url']


def main():
    os.makedirs('watch', exist_ok=True)
    first = not os.path.exists(STATE)
    seen = [] if first else json.load(open(STATE, encoding='utf-8'))
    known = set(seen)
    status, opened, changed = {}, [], first
    for kind, queries in QUERIES.items():
        for query in queries:
            try:
                items = fetch(query)
            except Exception as e:  # one failed query must not stop the others
                status[query] = {'error': type(e).__name__}
                continue
            hits = [i for i in items if official(i['host']) and relevant(kind, i['title'])]
            status[query] = {'items': len(items), 'official': sum(official(i['host']) for i in items), 'hits': len(hits)}
            for item in hits:
                if item['id'] in known:
                    continue
                known.add(item['id']); seen.append(item['id']); changed = True
                # The first run only learns what is already out there.
                if not first:
                    opened.append(open_issue(item, kind))
    event = os.environ.get('EVENT', '')
    if event == 'push' and os.path.exists(TRIAL):
        trial = json.load(open(TRIAL, encoding='utf-8'))
        if trial['id'] not in known:
            known.add(trial['id']); seen.append(trial['id']); changed = True
            opened.append(open_issue({'host': 'spa.gov.sa', 'date': datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M GMT'), **trial},
                                     trial['kind'], trial=True))
    if changed:
        json.dump(seen[-500:], open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    # The status file is the run's report; it is written when something happened, on a push, and once a month
    # (a scheduled workflow stops after 60 quiet days).
    now = datetime.now(timezone.utc)
    if changed or opened or event == 'push' or (now.day == 1 and now.hour == 0):
        json.dump({'ran': now.isoformat(timespec='minutes'), 'event': event, 'first': first, 'opened': opened, 'queries': status},
                  open(STATUS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'first': first, 'opened': opened, 'queries': status}, ensure_ascii=False, indent=1))
    if all('error' in s for s in status.values()):
        sys.exit('Every query failed')


if __name__ == '__main__':
    main()
