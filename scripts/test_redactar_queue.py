import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import redactar_noticias as editorial


def story(host, number, title, **metadata):
    return {'url': f'https://{host}/nfl/story/_/id/{number}/news', 'title': title, **metadata}


class NewsQueueTests(unittest.TestCase):
    def setUp(self):
        self.en1 = story('www.espn.com', 50599999, 'Rams are set to face the Packers')
        self.en2 = story('www.espn.com', 50599998, 'What to expect from the Chargers')
        self.es1 = story('espndeportes.espn.com', 50599997, 'Mundo NFL')
        self.es2 = story('www.espn.com.mx', 50599996, 'Steelers y Bengals')

    def test_serves_one_article_from_each_language_even_behind_newer_articles(self):
        queue = [self.en1, self.en2, self.es1, self.es2]
        self.assertEqual(editorial.select_queue(queue, 2), [self.es1, self.en1])
        self.assertEqual(editorial.select_queue(queue, 3), [self.es1, self.en1, self.en2])
        self.assertEqual(editorial.select_queue(queue, 4), [self.es1, self.en1, self.en2, self.es2])

    def test_preserves_feed_order_for_one_language_or_one_slot(self):
        self.assertEqual(editorial.select_queue([self.en1, self.en2], 3), [self.en1, self.en2])
        self.assertEqual(editorial.select_queue([self.es1, self.es2], 2), [self.es1, self.es2])
        self.assertEqual(editorial.select_queue([self.en1, self.es1, self.en2], 1), [self.en1])
        self.assertEqual(editorial.select_queue([], 2), [])

    def test_classifies_worker_queue_links_and_clearly_spanish_headlines(self):
        spanish_on_nfl = {'url': 'https://www.nfl.com/news/el-regreso-triunfal-de-lo-que-nunca-se-habia-ido',
                          'title': 'El regreso triunfal de lo que nunca se había ido'}
        mundo = {'url': 'https://nfl.com/mundo/noticias/rodgers', 'title': 'Rodgers'}
        as_story = {'url': 'https://mexico.as.com/nfl/noticia-n/', 'title': 'NFL'}
        nfl_english = {'url': 'https://nfl.com/news/a-receiver', 'title': 'NFL'}
        unknown = {'url': 'https://example.test/news/example', 'title': 'Un juego en la capital'}
        for item in (spanish_on_nfl, mundo, as_story):
            with self.subTest(item=item):
                self.assertEqual(editorial.queue_language(item), 'es')
        self.assertEqual(editorial.queue_language(nfl_english), 'en')
        self.assertIsNone(editorial.queue_language(unknown))
        self.assertEqual(editorial.select_queue([self.en1, unknown], 2), [self.en1, unknown])

    def test_respects_supported_language_metadata_when_supplied(self):
        listed_spanish = {**self.en2, 'language': 'es-MX'}
        listed_english = {**self.es1, 'language': 'EN-US'}
        queue = editorial.queue_items([listed_english, listed_spanish])
        self.assertEqual(editorial.select_queue(queue, 2), [listed_spanish, listed_english])
        self.assertEqual(editorial.queue_language({**self.en1, 'language': 'fr'}), 'en')

    def test_preflight_persists_only_selected_links_and_replay_uses_that_order(self):
        invalid = {'url': 'https://alice:example@www.espn.com/news', 'title': 'Un juego en la capital'}
        raw = [self.en1, {**self.en1, 'title': 'Duplicate'}, invalid, self.en2, {**self.es1, 'language': 'es-MX'}]
        site = editorial.SiteAPI.__new__(editorial.SiteAPI)
        site.request = Mock(return_value={'items': raw})
        site.original = Mock(return_value={'status': 'ready'})
        with tempfile.TemporaryDirectory() as directory:
            queue_file = Path(directory) / 'queue.json'
            args = SimpleNamespace(pending_only=True, queue_file=queue_file, limit=2, model_url=editorial.DEFAULT_MODEL)
            with redirect_stdout(StringIO()) as preflight:
                self.assertEqual(editorial.process(site, args), 0)
            self.assertEqual(preflight.getvalue(), '2\n')
            self.assertEqual(json.loads(queue_file.read_text(encoding='utf-8')), [self.es1, self.en1])
            site.request.assert_called_once_with('/api/noticias/resumenes/cola')
            site.original.assert_not_called()

            args.pending_only = False
            with redirect_stdout(StringIO()):
                self.assertEqual(editorial.process(site, args), 0)
            self.assertEqual(site.original.call_args_list, [call(item['url']) for item in (self.es1, self.en1)])
            site.request.assert_called_once_with('/api/noticias/resumenes/cola')

            args.limit = 1
            site.original.reset_mock()
            with redirect_stdout(StringIO()):
                self.assertEqual(editorial.process(site, args), 0)
            site.original.assert_called_once_with(self.es1['url'])

    def test_live_selection_and_original_language_are_independent(self):
        call_languages = []
        site = SimpleNamespace(pending=lambda: [self.en1, self.es1, self.en2], http=editorial.JsonHTTP(30), publish=Mock())
        site.original = Mock(side_effect=[{'paragraphs': ['Text of the original'], 'sourceWords': 4,
                                           'fingerprint': 'original-fingerprint', 'language': 'en'},
                                          {'status': 'unavailable'}])
        args = SimpleNamespace(pending_only=False, queue_file=None, limit=2, model_url=editorial.DEFAULT_MODEL)

        def summarize(http, args, title, paragraphs, source_words, language, published_at):
            call_languages.append(language)
            return ['Resumen en español.'], 6, 'Titular en español'

        with patch.object(editorial, 'summarize', summarize), redirect_stdout(StringIO()):
            self.assertEqual(editorial.process(site, args), 0)
        self.assertEqual(call_languages, ['en'])
        self.assertEqual(site.original.call_args_list, [call(item['url']) for item in (self.es1, self.en1)])
        site.publish.assert_called_once_with(self.es1['url'], ['Resumen en español.'], 4, 6, 'Titular en español',
                                             'original-fingerprint')


if __name__ == '__main__':
    unittest.main()
