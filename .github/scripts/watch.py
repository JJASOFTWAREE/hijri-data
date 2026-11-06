"""Watches official Saudi news for changes to the moon sighting or the school calendar, and opens an issue for the owner.

It only reads public headlines and never edits sighting.json or school.json: publishing waits for the owner.
Standard library only, so the workflow needs no installs.
"""
import html, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

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
FAILS = 'watch/fails.json'
ATTEMPTS, PAUSE = 3, 15
# The news search re-indexes old ministry pages as new; an article whose own date is older than this is skipped.
STALE_DAYS = 45
# The news search sometimes refuses a whole run; the owner is emailed only when it stays down for six hours.
ALARM_AFTER = 6


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


def download(url, data=None, headers=None):
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0', **(headers or {})})
            with urllib.request.urlopen(request, timeout=20) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(PAUSE)


def fetch(query):
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': 'ar', 'gl': 'SA', 'ceid': 'SA:ar'})
    body = download(url)
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


MONTHS = {'يناير': 1, 'فبراير': 2, 'مارس': 3, 'أبريل': 4, 'ابريل': 4, 'إبريل': 4, 'مايو': 5, 'يونيو': 6, 'يوليو': 7,
          'أغسطس': 8, 'اغسطس': 8, 'سبتمبر': 9, 'أكتوبر': 10, 'اكتوبر': 10, 'نوفمبر': 11, 'ديسمبر': 12}


def article_url(link):
    """The search gives its own redirect link; this asks it for the publisher's address, as its page script does."""
    gid = urllib.parse.urlparse(link).path.rsplit('/', 1)[-1]
    page = download(f'https://news.google.com/rss/articles/{gid}')
    sg = re.search(r'data-n-a-sg="([^"]+)"', page).group(1)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page).group(1)
    inner = ['garturlreq', [['X', 'X', ['X', 'X'], None, None, 1, 1, 'US:en', None, 1, None, None, None, None, None, 0, 1],
                            'X', 'X', 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], gid, int(ts), sg]
    body = 'f.req=' + urllib.parse.quote(json.dumps([[['Fbv4je', json.dumps(inner)]]]))
    reply = download('https://news.google.com/_/DotsSplashUi/data/batchexecute', data=body.encode(),
                     headers={'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'})
    return json.loads(json.loads(reply.split('\n\n', 1)[1])[0][2])[1]


def hijri_year(day):
    """Close enough to tell one Hijri year from the next (the mean year is 354.367 days from 16 July 622)."""
    return int((day - datetime(622, 7, 16, tzinfo=timezone.utc)).days / 354.367) + 1


def stale(text, now):
    """True only when every date the page shows is old; a page with no date, or any recent date, is never stale."""
    t = plain(text)
    days = []
    for d, m, y in re.findall(r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\s*م', t):
        try:
            days.append(datetime(int(y), MONTHS[m], int(d), tzinfo=timezone.utc))
        except ValueError:
            pass
    if days:
        return max(days) < now - timedelta(days=STALE_DAYS)
    gregorian = [int(y) for y in re.findall(r'(?<!\d)(\d{4})\s*م', t) + re.findall(r'(?<!\d)(\d{4})\s*/\s*\d{1,2}\s*/\s*\d{1,2}', t)]
    hijri = [int(y) for y in re.findall(r'(?<!\d)(1[34]\d\d)\s*ه', t)]
    return bool(gregorian and hijri) and max(gregorian) < now.year and max(hijri) < hijri_year(now)


def article_check(item, now):
    """Fills the publisher's link and says whether the article is old. Any failure keeps the alert."""
    try:
        url = article_url(item['link'])
        host = urllib.parse.urlparse(url).hostname or ''
        if not official(host):
            return False
        item['article'] = url
        return stale(re.sub(r'<[^>]+>', ' ', download(url)), now)
    except Exception:
        return False


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
        f'- **الرابط:** {item.get("article") or item["link"]}',
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
    status, opened, skipped, changed = {}, [], [], first
    now = datetime.now(timezone.utc)
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
                if first:
                    continue
                if article_check(item, now):
                    skipped.append(item.get('article') or item['link'])
                    continue
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
    if changed or opened or event == 'push' or (now.day == 1 and now.hour == 0):
        json.dump({'ran': now.isoformat(timespec='minutes'), 'event': event, 'first': first, 'opened': opened, 'skipped_old': skipped,
                   'queries': status}, open(STATUS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'first': first, 'opened': opened, 'skipped_old': skipped, 'queries': status}, ensure_ascii=False, indent=1))
    fails = json.load(open(FAILS, encoding='utf-8'))['count'] if os.path.exists(FAILS) else 0
    if all('error' in s for s in status.values()):
        fails += 1
        json.dump({'count': fails, 'last': now.isoformat(timespec='minutes')}, open(FAILS, 'w', encoding='utf-8'), indent=1)
        if fails >= ALARM_AFTER:
            sys.exit(f'Every query failed in {fails} runs in a row')
        print(f'Every query failed ({fails} in a row); the owner is alerted at {ALARM_AFTER}.')
    elif fails:
        os.remove(FAILS)


if __name__ == '__main__':
    main()
