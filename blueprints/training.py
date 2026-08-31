"""
training.py — the Training Centre: the officer course, hosted inside the system.

Officers learn the system fastest inside the system, so the course lives here
rather than in a file somebody has to be emailed. Three surfaces:

  GET /training              — the course outline: every session, up front
  GET /training/slides       — the teaching deck, full screen, for the room
  GET /training/<doc>        — the lesson plan and facilitator notes

Everything is behind a login: the pages are private to the cooperative, not
published to the web. The deck and the outline are open to any signed-in member,
because a member who wants to understand their own savings statement should not
have to ask. The facilitator's material is a separate duty — `training.facilitate`
in Settings → Task Assignment — because it is written for whoever runs the class.

The course content itself lives in `docs/training/` as one source of truth: the
same files are the shareable copies outside the system. Nothing is duplicated
into templates.
"""

import os
import re

from flask import Blueprint, abort, render_template
from flask_login import login_required

from utils import role_required

training = Blueprint('training', __name__, url_prefix='/training')

_DOC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'docs', 'training')

DECK_FILE = 'exco-training-deck.html'

# The written material, by URL slug. Anything not listed here cannot be reached,
# so no request can walk out of the training folder.
DOCUMENTS = {
    'lesson-plan': {
        'file':    'exco-training-lesson-plan.md',
        'title':   'Lesson plan',
        'summary': 'Outcomes by office, the timed agenda for both days, the exercises, '
                   'and the sign-off checklist.',
    },
    'facilitator-notes': {
        'file':    'exco-training-facilitator-notes.md',
        'title':   'Facilitator notes',
        'summary': 'What to say, what to click, what people get wrong, and the answers '
                   'to the questions the room will ask.',
    },
}

# ── The course, as the outline page shows it ─────────────────────────────────
#
# This is the list officers see before anything else, so they know the shape of
# the two days before the first slide. It mirrors the lesson plan; keep them
# together when either changes.

COURSE = [
    {
        'day':      'Day one',
        'theme':    'The work you do every week',
        'length':   'About 3½ hours',
        'summary':  'The screens you touch every week, and who is allowed to touch them.',
        'sessions': [
            ('Why we moved from books to the computer',
             'One place for everything, nothing rubbed out, and we decide who does what.'),
            ('Logging in safely, and finding your way around',
             'Your password, the six-digit code on your phone, the dashboard and the menu.'),
            ('Adding members and getting them started',
             'The three details that matter, sending invitations, and uploading many at once.'),
            ('Taking savings, and fixing a mistake',
             'One payment, the monthly upload, and how to put a wrong upload right.'),
            ('Loans, from request to money in hand',
             'Guarantors, then Secretary, then Treasurer, then President — and repayments.'),
            ('How members hear from us',
             'The phone app, text messages, email, and what each one costs the society.'),
        ],
    },
    {
        'day':      'Day two',
        'theme':    'What happens to the money',
        'length':   'About 3½ hours',
        'summary':  'Nobody is turned into an accountant. You learn to read what the '
                    'system already wrote, and how to check it.',
        'sessions': [
            ('Big words, small meanings',
             'Entry, debit and credit, ledger, balance, trial balance, reversal — in plain words.'),
            ('What the button wrote',
             'Savings, a loan paid out, and a repayment — followed through with real amounts.'),
            ('Correcting mistakes the right way',
             'We cancel with a reason; we never delete. And money that arrived but is not yet used.'),
            ('End-of-month checks',
             'Six checks, in order, and what closing a month protects.'),
            ('Sharing the profit',
             'How a dividend is worked out, and what must be agreed before anybody declares.'),
            ('Staying out of trouble',
             'What an auditor asks for, sharing duties safely, and keeping accounts safe.'),
            ('Everyone tries three tasks alone',
             'Each officer does their own three tasks without help.'),
        ],
    },
]

EXERCISES = [
    ('1', 'Add and invite',        'Add a member, send the invitation, complete their details.'),
    ('2', 'The bad upload',        'Upload a wrong savings list and put it right, in the right order.'),
    ('3', 'One loan, four officers', 'Take a loan from guarantors to payment, then repay it.'),
    ('4', 'Find the entry',        'Open what the system wrote for that repayment and read both sides.'),
    ('5', 'Cancel it',             'Cancel the repayment with a reason, then find the reason again.'),
    ('6', 'End of month',          'Do the six checks and report one thing that does not agree.'),
    ('7', 'Share the profit',      'Work out a dividend and explain every split before declaring.'),
]


def _read(filename):
    path = os.path.join(_DOC_DIR, filename)
    if not os.path.isfile(path):
        return None
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


# ── Markdown ─────────────────────────────────────────────────────────────────

def _render_markdown(text):
    """Turn the course markdown into HTML.

    Uses the `markdown` package when it is installed. A deployment that has not
    been rebuilt since this page was added still shows the material as plain
    text rather than an error — the training is never the reason a page 500s.
    """
    try:
        import markdown as _md
        return _md.markdown(text, extensions=['tables', 'toc', 'sane_lists']), True
    except Exception:
        from markupsafe import escape
        return '<pre class="training-plain">%s</pre>' % escape(text), False


def _first_heading(text, fallback):
    match = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def _relink(html):
    """Point the documents' cross-references at the pages that serve them.

    The material is written as files that link to each other by filename. On the
    web those links would 404, so the two we host become real links and every
    other file reference is left as plain text rather than a dead one.
    """
    from flask import url_for

    for slug, meta in DOCUMENTS.items():
        html = html.replace('href="%s"' % meta['file'],
                            'href="%s"' % url_for('training.document', slug=slug))
    return re.sub(r'<a href="[^"]*\.md"[^>]*>(.*?)</a>', r'\1', html, flags=re.DOTALL)


# ── Routes ───────────────────────────────────────────────────────────────────

@training.route('/')
@login_required
def index():
    """The course outline — what the two days cover, before anything else."""
    return render_template('training/index.html',
                           course=COURSE,
                           exercises=EXERCISES,
                           documents=DOCUMENTS,
                           deck_available=_read(DECK_FILE) is not None)


@training.route('/slides')
@login_required
def slides():
    """The teaching deck, full screen — the same deck used in the room."""
    deck = _read(DECK_FILE)
    if deck is None:
        abort(404)
    return render_template('training/slides.html', deck_html=deck)


@training.route('/<slug>')
@login_required
@role_required('admin', 'secretary', 'treasurer', 'exco')
def document(slug):
    """The written material for whoever runs the class."""
    meta = DOCUMENTS.get(slug)
    if not meta:
        abort(404)
    text = _read(meta['file'])
    if text is None:
        abort(404)
    body, rich = _render_markdown(text)
    if rich:
        body = _relink(body)
    return render_template('training/doc.html',
                           title=_first_heading(text, meta['title']),
                           summary=meta['summary'],
                           body=body,
                           rich=rich,
                           slug=slug,
                           documents=DOCUMENTS)
