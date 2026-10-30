from datetime import date
from run.backfill_fw20_prices import advertised_pages, gather_history, history_url


def sample(day, close):
    return f'<tr><td>{day}</td><td>{close}</td><td>{close}</td><td>{close}</td><td>{close}</td><td>2</td></tr>'


def page(day, close, next_page=None):
    nxt = (f'<a href="/notowania-historyczne/FW20H2720,{next_page}">next</a>'
           if next_page else '')
    return ('<table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th>'
            '<th>Zamknięcie</th><th>Wolumen</th></tr>' + sample(day, close) +
            '</table>' + nxt)


class Session:
    def __init__(self, pages):
        self.pages = pages
        self.visited = []

    def get(self, url, timeout):
        self.visited.append(url)
        class Response:
            def __init__(self, content):
                self.text = content
            def raise_for_status(self):
                pass
        return Response(self.pages[url])


def test_url_and_advertised_pagination():
    assert history_url('FW20H2720', 2).endswith('FW20H2720,2')
    assert advertised_pages('<a href="FW20H2720%2C2">2</a>', 'FW20H2720') == {2}
    assert advertised_pages('<a href="/notowania-historyczne/FW20H2720,3">3</a>', 'FW20H2720') == {3}


def test_all_pages_collected():
    s = Session({history_url('FW20H2720', 1): page('28.10.2026', '4200', 2),
                 history_url('FW20H2720', 2): page('01.07.2026', '3900')})
    rows, n, status = gather_history(s, 'FW20H2720', through=date(2026, 10, 28), delay=0)
    assert n == 2
    assert [r['date'] for r in rows] == [date(2026, 7, 1), date(2026, 10, 28)]
    assert status == 'SOURCE_NAVIGATION_EXHAUSTED'


def test_repeated_pages_rejected():
    s = Session({history_url('FW20H2720', 1): page('28.10.2026', '4200', 2),
                 history_url('FW20H2720', 2): page('28.10.2026', '4200')})
    import pytest
    with pytest.raises(RuntimeError, match='did not advance'):
        gather_history(s, 'FW20H2720', through=date(2026, 10, 28), delay=0)


def test_max_pages_fails_closed():
    s = Session({history_url('FW20H2720', 1): page('28.10.2026', '4200', 2)})
    import pytest
    with pytest.raises(RuntimeError, match='max_pages'):
        gather_history(s, 'FW20H2720', through=date(2026, 10, 28), delay=0, max_pages=1)
