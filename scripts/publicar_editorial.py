#!/usr/bin/env python3
"""Publica una edición humana a través del flujo autorizado de Niftl."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from redactar_noticias import (PLAIN_LINE, InvalidDraft, JsonHTTP, RequestFailure, SiteAPI, StopWork,
                               clearly_spanish, editorial_language, normalized_words, publication_context,
                               trusted_url, validate, word_count)

FIELDS = {'url', 'fingerprint', 'sourceLanguage', 'sourceWords', 'translatedWords', 'titleEs', 'paragraphs'}


def parse_editorial(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 60_000:
        raise InvalidDraft('Entrega el JSON de una sola edición.')
    try:
        entry = json.loads(raw)
    except ValueError:
        raise InvalidDraft('El JSON de la edición es inválido.') from None
    if not isinstance(entry, dict) or set(entry) != FIELDS:
        raise InvalidDraft('Faltan campos de la edición o hay campos ajenos al formato.')
    url = entry['url']
    parts = trusted_url(url if isinstance(url, str) else None)
    if parts.scheme != 'https' or parts.port is not None or parts.query or len(url) > 1800:
        raise InvalidDraft('Usa la URL canónica HTTPS de la noticia.')
    fingerprint = entry['fingerprint']
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[\da-f]{64}', fingerprint, re.ASCII):
        raise InvalidDraft('Se requiere la huella SHA-256 del artículo leído.')
    if entry['sourceLanguage'] not in ('en', 'es'):
        raise InvalidDraft('Indica el idioma del original como en o es.')
    for key in ('sourceWords', 'translatedWords'):
        if type(entry[key]) is not int or entry[key] < 2:
            raise InvalidDraft('Los recuentos del original y del texto completo en español deben ser enteros positivos.')
    title = entry['titleEs']
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 300 or PLAIN_LINE.search(title):
        raise InvalidDraft('Se requiere un titular en español en una sola línea.')
    paragraphs = entry['paragraphs']
    if (not isinstance(paragraphs, list) or not paragraphs
            or any(not isinstance(p, str) or not p.strip() for p in paragraphs)):
        raise InvalidDraft('Se requiere al menos un párrafo de resumen.')
    entry['titleEs'] = title.strip()
    return entry


def publish_editorial(site, entry):
    url = entry['url']
    original = site.original(url)
    if not isinstance(original, dict):
        raise RequestFailure('Original', 'respuesta sin cuerpo verificable')
    if original.get('status') == 'ready':
        raise RequestFailure('Original', 'ya hay una edición publicada para esta nota')
    paragraphs = original.get('paragraphs')
    if not isinstance(paragraphs, list) or not paragraphs or any(not isinstance(p, str) or not p.strip() for p in paragraphs):
        raise RequestFailure('Original', 'no está disponible el artículo completo')
    body = '\n\n'.join(paragraphs)
    source_words = entry['sourceWords']
    fingerprint = entry['fingerprint']
    if (original.get('fingerprint') != fingerprint or hashlib.sha256(body.encode('utf-8')).hexdigest() != fingerprint
            or original.get('sourceWords') != source_words or word_count(body) != source_words):
        raise RequestFailure('Original', 'cambiaron la huella o el recuento del artículo')
    language = editorial_language(original.get('language'), url)
    source_is_spanish = language == 'es' or clearly_spanish(body)
    if entry['sourceLanguage'] != ('es' if source_is_spanish else 'en'):
        raise InvalidDraft('El idioma indicado difiere del cuerpo que entregó el sitio.')
    translated_words = entry['translatedWords']
    if source_is_spanish and translated_words != source_words:
        raise InvalidDraft('Cuando el artículo ya está en español, ambos recuentos deben coincidir.')
    title = entry['titleEs']
    original_title = original.get('title') or ''
    if not source_is_spanish and normalized_words(title) == normalized_words(original_title) and not clearly_spanish(title):
        raise InvalidDraft('El titular de la fuente requiere traducción al español.')
    summary = validate(entry['paragraphs'], body, original_title, translated_words)
    if not clearly_spanish(' '.join(summary)):
        raise InvalidDraft('El resumen debe estar redactado en español.')
    count = word_count(' '.join(summary))
    published = site.publish(url, summary, source_words, translated_words, title, fingerprint)
    expected = {'status': 'ready', 'language': 'es', 'titleEs': title, 'paragraphs': summary,
                'sourceWords': source_words, 'translatedWords': translated_words, 'summaryWords': count}
    for field, receipt_field in {'sourceUrl': 'url', 'source': 'source', 'publishedAt': 'publishedAt'}.items():
        if original.get(receipt_field):
            expected[field] = original[receipt_field]
    if (not isinstance(published, dict) or any(published.get(key) != value for key, value in expected.items())
            or not publication_context(published.get('summarizedAt'))):
        raise RequestFailure('Publicación', 'la edición devuelta por el sitio difiere de la enviada')
    readback = site.original(url)
    if not isinstance(readback, dict) or readback.get('status') != 'ready':
        raise RequestFailure('Lectura de retorno', 'el sitio no confirmó la edición guardada')
    return count


def main():
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        entry = parse_editorial(event['inputs']['editorial'])
    except (KeyError, OSError, TypeError, ValueError, InvalidDraft):
        print('El evento necesita el JSON de una edición con recuentos y huella verificables.', file=sys.stderr)
        return 1
    try:
        count = publish_editorial(SiteAPI(JsonHTTP(45)), entry)
        print(f'Edición publicada y confirmada por GET ({count} de {entry["translatedWords"]} palabras en español).')
        return 0
    except (RequestFailure, ValueError, InvalidDraft) as error:
        print(str(error), file=sys.stderr)
    except StopWork:
        print('Se agotó el tiempo antes de confirmar la edición.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
