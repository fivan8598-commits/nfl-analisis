import math
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import redactar_noticias as editorial


ORIGINAL = [
    'The coach said on Wednesday that the receiver would miss the next game with an ankle injury. '
    'He planned to evaluate the injury again after the game. The club did not set a date for his return.',
    'Rookie Mateo Vega took most of the snaps with the starters during practice. '
    'The coordinator said the team would divide the work among the other receivers and decide who would start before kickoff.',
]
TRANSLATED = [
    'El entrenador explicó el miércoles que el receptor se perdería el siguiente partido debido a una lesión en el tobillo. '
    'Tenía previsto someter al jugador a una nueva evaluación una vez terminado ese encuentro. '
    'Por su parte, el club no fijó una fecha para que el futbolista pudiera volver a jugar.',
    'El novato Mateo Vega participó en la mayoría de las jugadas con el grupo de titulares durante la sesión de entrenamiento. '
    'El coordinador afirmó que el conjunto repartiría el trabajo entre los demás receptores y que resolvería quién empezaría el partido antes de la patada inicial.',
]
DRAFTS = [
    'La lesión de tobillo dejaría al receptor fuera del próximo encuentro, según el entrenador. El club evaluaría su evolución después del juego y mantiene abierto el regreso.',
    'Mateo Vega encabezó las repeticiones con los titulares. El coordinador prevé repartir el trabajo entre varios receptores y definir la titularidad antes del juego.',
]
TITLE = 'Receiver expected to miss next game'
TITLE_ES = 'El receptor se perdería el próximo partido'
ARGS = SimpleNamespace(corrections=1)


class EditorialTest(unittest.TestCase):
    def test_completes_all_translation_before_the_draft_and_measures_spanish(self):
        calls = []
        translations = iter([{'paragraphs': [TRANSLATED[0]], 'titleEs': TITLE_ES}, {'paragraphs': [TRANSLATED[1]]}])
        drafts = iter(DRAFTS)

        def model(http, args, stage, system, data, max_tokens, **opts):
            calls.append((stage, data.copy()))
            if stage == 'Traducción':
                return next(translations)
            if stage == 'Modelo local':
                return {'paragraphs': [next(drafts)]}
            return {'verdict': 'ok'}

        source_words = editorial.word_count(' '.join(ORIGINAL))
        with patch.object(editorial, 'TRANSLATION_CHARS', max(map(len, ORIGINAL)) + 1), patch.object(editorial, 'model_json', model):
            summary, translated_words, title_es = editorial.summarize(None, ARGS, TITLE, ORIGINAL, source_words, 'en')
        self.assertEqual([stage for stage, _ in calls], ['Traducción', 'Revisión de traducción'] * 2 + ['Modelo local', 'Revisión editorial'] * 2)
        self.assertEqual(calls[4][1]['texto_en_espanol'], TRANSLATED[0])
        self.assertNotIn('texto_original', calls[4][1])
        self.assertEqual(calls[5][1]['texto_original'], ORIGINAL[0])
        self.assertEqual(title_es, TITLE_ES)
        self.assertEqual(translated_words, editorial.word_count(' '.join(TRANSLATED)))
        count = editorial.word_count(' '.join(summary))
        self.assertGreater(count, math.floor(source_words * .55))
        self.assertTrue(math.ceil(translated_words * .45) <= count <= math.floor(translated_words * .55))

    def test_preserves_the_full_spanish_body(self):
        calls = []

        def model(http, args, stage, system, data, max_tokens, **opts):
            calls.append((stage, data))
            return {'paragraphs': DRAFTS} if stage == 'Modelo local' else {'verdict': 'ok'}

        count = editorial.word_count(' '.join(TRANSLATED))
        with patch.object(editorial, 'model_json', model):
            summary, words, title = editorial.summarize(None, ARGS, TITLE_ES, TRANSLATED, count, 'es')
        self.assertEqual([stage for stage, _ in calls], ['Modelo local', 'Revisión editorial'])
        self.assertEqual(calls[0][1]['texto_en_espanol'], ' '.join(TRANSLATED))
        self.assertEqual((summary, words, title), (DRAFTS, count, TITLE_ES))

    def test_does_not_retranslate_a_spanish_body_mislabeled_as_english(self):
        nfl_title = 'El regreso triunfal de lo que nunca se había ido'
        with patch.object(editorial, 'translate_fragment') as translate:
            chunks, title = editorial.spanish_article(None, ARGS, nfl_title, TRANSLATED, 'en')
        translate.assert_not_called()
        self.assertEqual(title, nfl_title)
        self.assertEqual(chunks, [(' '.join(TRANSLATED), ' '.join(TRANSLATED))])

    def test_translates_an_english_body_and_preserves_an_already_spanish_headline(self):
        nfl_title = 'El regreso triunfal de lo que nunca se había ido'
        with patch.object(editorial, 'translate_fragment', return_value=(TRANSLATED, None)) as translate:
            chunks, title = editorial.spanish_article(None, ARGS, nfl_title, ORIGINAL, 'en')
        translate.assert_called_once_with(None, ARGS, ORIGINAL, None)
        self.assertEqual(title, nfl_title)
        self.assertEqual(chunks, [(' '.join(ORIGINAL), ' '.join(TRANSLATED))])
        self.assertFalse(editorial.clearly_spanish('How to watch the Carolina Panthers and the Tampa Bay Buccaneers'))

    def test_rejects_missing_text_and_changed_numeric_details(self):
        with patch.object(editorial, 'model_json', return_value={'paragraphs': [TRANSLATED[0]], 'titleEs': TITLE_ES}):
            with self.assertRaisesRegex(editorial.InvalidDraft, 'todos los párrafos'):
                editorial.translate_fragment(None, ARGS, ORIGINAL, TITLE)
        with self.assertRaisesRegex(editorial.InvalidDraft, 'modifica cifras'):
            editorial.check_translation('Vega ran 5.3 yards for the club.', 'Vega recorrió 53 yardas para el club.')
        with self.assertRaisesRegex(editorial.InvalidDraft, 'en español'):
            editorial.check_translation(ORIGINAL[0], ORIGINAL[0])

    def test_table_statistics_accept_valid_grouping_and_written_numbers(self):
        source = 'Baker Mayfield: 1,209 passing yards, five passing touchdowns.'
        for translation in ['Baker Mayfield: 1209 yardas de pase y 5 pases de touchdown.',
                            'Baker Mayfield: 1.209 yardas de pase y cinco touchdowns por pase.',
                            'Baker Mayfield: 1,209 yardas por pase y 5 pases de touchdown.']:
            self.assertEqual(editorial.check_translation(source, translation), translation)
        self.assertTrue(editorial.same_numerals('Gained an average of 5.30 yards', 'Promedió 5,3 yardas'))
        for translation in ['Baker Mayfield: 1290 yardas de pase y 5 pases de touchdown.',
                            'Baker Mayfield: 1209 yardas de pase y 7 pases de touchdown.',
                            'Baker Mayfield: 1209 yardas en 2 juegos y 5 pases de touchdown.']:
            with self.assertRaisesRegex(editorial.InvalidDraft, 'modifica cifras'):
                editorial.check_translation(source, translation)

    def test_preserves_signed_odds_without_treating_a_score_as_negative(self):
        self.assertTrue(editorial.same_numerals('Priced at -115 before the 27-0 win', 'Cuota −115 antes del triunfo 27−0'))
        self.assertTrue(editorial.same_numerals('Went 5-4 with odds of +115', 'Marca de 5–4 y momio +115'))
        self.assertFalse(editorial.same_numerals('Priced at -115', 'Momio de +115'))
        self.assertFalse(editorial.same_numerals('Priced at +115', 'Momio de 115'))
        with self.assertRaisesRegex(editorial.InvalidDraft, 'cifras sin respaldo'):
            editorial.validate(['El precio de la selección subió a +115.'], 'La selección cotizó a −115.', TITLE_ES, 16)

    def test_matches_clock_notation_without_permitting_a_different_time(self):
        self.assertTrue(editorial.same_numerals('The game airs at 8:15 p.m. ET on ESPN2.', 'ESPN2 transmite el juego a las 20:15 (hora del Este).'))
        self.assertTrue(editorial.same_numerals('The event runs 12:00 a.m. to 12:00 p.m.', 'El evento va de las 00:00 a las 12:00.'))
        self.assertFalse(editorial.same_numerals('Kickoff is 8:15 p.m. ET.', 'Inicio a las 20:45 horas.'))
        self.assertFalse(editorial.same_numerals('Kickoff is 8:15 a.m. ET.', 'Inicio a las 20:15 horas.'))

    def test_rejects_verbatim_text_even_across_a_bilingual_summary(self):
        with self.assertRaisesRegex(editorial.InvalidDraft, 'ocho o más'):
            editorial.validate([ORIGINAL[0]], TRANSLATED[0], TITLE_ES, 105, original=ORIGINAL[0])
        with self.assertRaisesRegex(editorial.InvalidDraft, 'ocho o más'):
            editorial.validate([TRANSLATED[0]], TRANSLATED[0], TITLE_ES, 105, original=ORIGINAL[0])

    def test_publication_contains_the_summary_and_counts_without_full_translation(self):
        site = editorial.SiteAPI.__new__(editorial.SiteAPI)
        path = 'https://www.espn.com/nfl/story/_/id/12345678/example'
        with patch.object(site, 'request', return_value={'status': 'ready'}) as request:
            site.publish(path, DRAFTS, 80, 107, TITLE_ES, 'fingerprint')
        request.assert_called_once_with('/api/noticias/resumen', method='PUT', params={'url': path}, payload={
            'paragraphs': DRAFTS, 'titleEs': TITLE_ES, 'sourceWords': 80, 'translatedWords': 107, 'fingerprint': 'fingerprint',
        })

    def test_long_article_leaves_editorial_time_for_the_next_item(self):
        clock = [1000]
        first, second = ('https://www.espn.com/nfl/story/_/id/12345678/one', 'https://www.espn.com/nfl/story/_/id/12345679/two')
        original = {'paragraphs': ORIGINAL, 'sourceWords': editorial.word_count(' '.join(ORIGINAL)),
                    'fingerprint': 'fingerprint', 'language': 'en', 'title': TITLE}

        def draft(http, args, title, *rest):
            self.assertEqual(title, TITLE)
            if clock[0] == 1000:
                clock[0] = http.deadline + 1
                raise editorial.StopWork()
            self.assertGreater(http.remaining(), 200)
            return DRAFTS, 97, TITLE_ES

        args = SimpleNamespace(pending_only=False, queue_file=None, limit=2, model_url=editorial.DEFAULT_MODEL)
        with patch.object(editorial.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(editorial, 'summarize', draft):
            site = SimpleNamespace(http=editorial.JsonHTTP(800), pending=lambda: [{'url': first, 'title': 'Uno'}, {'url': second, 'title': 'Dos'}],
                                   original=lambda url: original, publish=lambda url, *payload: self.assertEqual(url, second))
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(editorial.process(site, args), 0)


if __name__ == '__main__':
    unittest.main()
