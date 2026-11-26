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
        translations = iter([{'paragraphs': [{'id': 1, 'text': TRANSLATED[0]}], 'titleEs': TITLE_ES},
                             {'paragraphs': [{'id': 1, 'text': TRANSLATED[1]}]}])

        def model(http, args, stage, system, data, max_tokens, **opts):
            calls.append((stage, data.copy()))
            if stage == 'Traducción':
                return next(translations)
            if stage == 'Modelo local':
                return {'paragraphs': DRAFTS}
            return {'verdict': 'ok'}

        source_words = editorial.word_count(' '.join(ORIGINAL))
        with patch.object(editorial, 'TRANSLATION_CHARS', max(map(len, ORIGINAL)) + 1), patch.object(editorial, 'model_json', model):
            summary, translated_words, title_es = editorial.summarize(None, ARGS, TITLE, ORIGINAL, source_words, 'en')
        self.assertEqual([stage for stage, _ in calls], ['Traducción', 'Revisión de traducción'] * 2 + ['Modelo local', 'Revisión editorial'])
        self.assertEqual(calls[4][1]['texto_en_espanol'], '\n'.join(TRANSLATED))
        self.assertNotIn('texto_original', calls[4][1])
        self.assertEqual(calls[5][1]['texto_original'], '\n'.join(ORIGINAL))
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
        self.assertEqual(chunks, [('\n'.join(ORIGINAL), '\n'.join(TRANSLATED))])
        self.assertFalse(editorial.clearly_spanish('How to watch the Carolina Panthers and the Tampa Bay Buccaneers'))

    def test_rejects_missing_text_and_changed_numeric_details(self):
        with patch.object(editorial, 'model_json', return_value={'paragraphs': [TRANSLATED[0]], 'titleEs': TITLE_ES}):
            with self.assertRaisesRegex(editorial.InvalidDraft, 'todos los párrafos'):
                editorial.translate_fragment(None, ARGS, ORIGINAL, TITLE)
        with self.assertRaisesRegex(editorial.InvalidDraft, 'modifica cifras'):
            editorial.check_translation('Vega ran 5.3 yards for the club.', 'Vega recorrió 53 yardas para el club.')
        with self.assertRaisesRegex(editorial.InvalidDraft, 'en español'):
            editorial.check_translation(ORIGINAL[0], ORIGINAL[0])

    def test_multiple_short_rows_keep_the_translation_limit_and_relative_order(self):
        rows = [f'Unknown row {i}' for i in range(37)]
        groups = editorial.article_chunks(rows, 110, preserve_paragraphs=True, max_blocks=4)
        self.assertEqual([part for group in groups for part in group], rows)
        self.assertTrue(all(len(group) <= 4 and len(' '.join(group)) <= 110 for group in groups))
        self.assertEqual(len(groups[-1]), 1)

    def test_translation_splits_after_reordered_ids_and_only_retries_the_failed_portion(self):
        original = ['No. 10', 'No. 20', 'No. 30', 'No. 40']
        calls = []
        def model(http, args, stage, system, data, max_tokens, **opts):
            if stage == 'Revisión de traducción':
                self.assertTrue(all(block['id'] > 0 for block in data['bloques']))
                return {'verdict': 'ok'}
            source = data['parrafos_originales']
            ids = [block['id'] for block in source]
            calls.append((ids, data.get('titular_original')))
            if ids == [3, 4]:
                raise editorial.RequestFailure(stage, 'servicio inaccesible o tiempo agotado')
            output = [{'id': block['id'], 'text': 'Número '+block['text'].split()[-1]} for block in source]
            if ids == [1, 2, 3, 4]:
                output.reverse()
            return {'paragraphs': output, 'titleEs': TITLE_ES}
        with patch.object(editorial, 'model_json', model):
            translated, headline = editorial.translate_fragment(None, ARGS, original, TITLE)
        self.assertEqual(calls, [([1, 2, 3, 4], TITLE), ([1, 2], TITLE), ([3, 4], None), ([3], None), ([4], None)])
        self.assertEqual(translated, ['Número 10', 'Número 20', 'Número 30', 'Número 40'])
        self.assertEqual(headline, TITLE_ES)

    def test_translation_preserves_the_full_uniform_table_and_checks_unknown_values(self):
        rows = ['Arizona Cardinals', 'Helmet: Black', 'Jersey: Black', 'Pants: TBA',
                'Dallas Cowboys', 'Helmet: White', 'Jersey: Navy', 'Pants: shade number 7']
        expected = ['Arizona Cardinals', 'Casco: negro', 'Camiseta: negro', 'Pantalón: por anunciar',
                    'Dallas Cowboys', 'Casco: blanco', 'Camiseta: azul marino', 'Pantalón: tono número 7']
        with patch.object(editorial, 'translate_fragment', return_value=([expected[-1]], TITLE_ES)) as translate:
            parts, title = editorial.spanish_article(None, ARGS, TITLE, rows, 'en')
        translate.assert_called_once_with(None, ARGS, [rows[-1]], TITLE)
        self.assertEqual(parts, [('\n'.join(rows), '\n'.join(expected))])
        self.assertEqual(title, TITLE_ES)

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

    def test_offers_the_model_exact_spans_and_rechecks_its_rewritten_draft(self):
        calls = []

        def model(http, args, stage, system, data, max_tokens, **opts):
            calls.append((stage, system, data.copy()))
            if stage == 'Revisión editorial':
                return {'verdict': 'ok'}
            return {'paragraphs': [DRAFTS[0] if data.get('coincidencias_literales') else TRANSLATED[0]]}

        with patch.object(editorial, 'model_json', model):
            output = editorial.model_draft(None, ARGS, TITLE_ES, TRANSLATED[0], ORIGINAL[0], len(TRANSLATED[0].split()), 1, 1, None)
        self.assertEqual(output, [DRAFTS[0]])
        _, system, correction = calls[1]
        self.assertIn('reformula cada coincidencia literal', system)
        self.assertEqual(correction['borrador_para_reescribir'], [TRANSLATED[0]])
        self.assertIn('El entrenador explicó el miércoles que el receptor', correction['coincidencias_literales'])
        self.assertEqual(calls[2][0], 'Revisión editorial')
        self.assertEqual(calls[2][2]['texto_original'], ORIGINAL[0])

    def test_rejects_rewrite_that_keeps_a_copied_passage(self):
        with patch.object(editorial, 'model_json', return_value={'paragraphs': [TRANSLATED[0]]}) as model:
            with self.assertRaises(editorial.LiteralDraft):
                editorial.model_draft(None, ARGS, TITLE_ES, TRANSLATED[0], ORIGINAL[0], len(TRANSLATED[0].split()), 1, 1, None)
        self.assertEqual(model.call_count, 2)
        source = 'El informe cita hechos bien documentados en distintas páginas.'
        with self.assertRaises(editorial.LiteralDraft):
            editorial.validate([source.replace('documentados', 'docu\u0301mentados')], source, TITLE_ES, 20)

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
