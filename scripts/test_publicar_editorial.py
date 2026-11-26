import hashlib
import json
import unittest
from unittest.mock import Mock, call

from publicar_editorial import parse_editorial, publish_editorial
from redactar_noticias import InvalidDraft, RequestFailure, word_count


class EditorialPublicationTests(unittest.TestCase):
    def setUp(self):
        source = ['League officials met with coaches on Monday to explain changes to the upcoming schedule. '
                  'The clubs learned that the new calendar would affect several divisional games.',
                  'The meeting focused on how teams will organize their preparations for the upcoming matches. '
                  'Final publication of the dates is expected later, according to the league spokesman. '
                  'Officials also explained how the clubs could ask for more information.']
        body = '\n\n'.join(source)
        summary = ['La liga presentó a los entrenadores un calendario con ajustes para los próximos duelos divisionales. '
                   'Los clubes revisarán cómo preparar esos partidos y podrán pedir más detalles. '
                   'Según un portavoz de la liga, las fechas definitivas se anunciarán más adelante.']
        url = 'https://www.espn.com/nfl/story/_/id/55443322/schedule-update'
        self.entry = {'url': url, 'fingerprint': hashlib.sha256(body.encode()).hexdigest(), 'sourceLanguage': 'en',
                      'sourceWords': word_count(body), 'translatedWords': 82,
                      'titleEs': 'La liga presentó cambios de calendario a los entrenadores', 'paragraphs': summary}
        self.receipt = {'url': url, 'language': 'en', 'sourceWords': self.entry['sourceWords'],
                        'fingerprint': self.entry['fingerprint'], 'title': 'League officials meet with coaches',
                        'source': 'ESPN', 'publishedAt': '2026-11-26T00:30:00.000Z', 'paragraphs': source}
        self.ready = {'status': 'ready', 'language': 'es', 'titleEs': self.entry['titleEs'], 'paragraphs': summary,
                      'sourceWords': self.entry['sourceWords'], 'translatedWords': 82,
                      'summaryWords': word_count(' '.join(summary)), 'sourceUrl': url, 'source': 'ESPN',
                      'publishedAt': self.receipt['publishedAt'], 'summarizedAt': '2026-11-26T01:00:00.000Z'}

    def site(self, receipt=None, ready=None, confirm=None):
        site = Mock()
        site.original.side_effect = [self.receipt if receipt is None else receipt,
                                     {'status': 'ready'} if confirm is None else confirm]
        site.publish.return_value = self.ready if ready is None else ready
        return site

    def test_new_publication_verifies_the_returned_metadata_and_reads_ready_separately(self):
        site = self.site()
        entry = parse_editorial(json.dumps(self.entry))
        count = publish_editorial(site, entry)
        self.assertEqual(count, self.ready['summaryWords'])
        self.assertEqual(site.method_calls, [
            call.original(entry['url']),
            call.publish(entry['url'], entry['paragraphs'], entry['sourceWords'], entry['translatedWords'],
                         entry['titleEs'], entry['fingerprint']),
            call.original(entry['url']),
        ])

    def test_ready_unavailable_or_shifted_source_never_reach_publication(self):
        for receipt in [{'status': 'ready'}, {'status': 'unavailable'},
                        {**self.receipt, 'fingerprint': '0' * 64},
                        {**self.receipt, 'paragraphs': self.receipt['paragraphs'] + ['A sudden change.']},
                        {**self.receipt, 'sourceWords': 1000}]:
            with self.subTest(receipt_status=receipt.get('status')):
                site = self.site(receipt=receipt)
                with self.assertRaises(RequestFailure):
                    publish_editorial(site, self.entry)
                site.publish.assert_not_called()

    def test_wrong_language_copied_content_bad_length_and_untranslated_title_are_rejected(self):
        for entry in [{**self.entry, 'sourceLanguage': 'es'}, {**self.entry, 'translatedWords': 300},
                      {**self.entry, 'paragraphs': self.receipt['paragraphs'][:1]},
                      {**self.entry, 'titleEs': self.receipt['title']}]:
            with self.subTest(issue=list(entry)):
                site = self.site()
                with self.assertRaises(InvalidDraft):
                    publish_editorial(site, entry)
                site.publish.assert_not_called()

    def test_detects_a_concurrent_publication_or_a_missing_readback(self):
        replaced = self.site(ready={**self.ready, 'titleEs': 'Otra edición publicada'})
        with self.assertRaisesRegex(RequestFailure, 'difiere'):
            publish_editorial(replaced, self.entry)
        self.assertEqual(replaced.original.call_count, 1)
        missing = self.site(confirm={'status': 'processing'})
        with self.assertRaisesRegex(RequestFailure, 'Lectura de retorno'):
            publish_editorial(missing, self.entry)
        self.assertEqual(missing.original.call_count, 2)

    def test_dispatch_payload_contains_only_the_verified_summary(self):
        for update in [{'original': self.receipt['paragraphs']}, {'sourceWords': True},
                       {'fingerprint': 'short'}, {'url': self.entry['url'] + '?amp=1'},
                       {'titleEs': '<h1>Un titular</h1>'}, {'paragraphs': 'text'}]:
            with self.subTest(invalid=list(update)):
                with self.assertRaises((InvalidDraft, ValueError)):
                    parse_editorial(json.dumps({**self.entry, **update}))
