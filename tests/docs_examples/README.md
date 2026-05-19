# Docs examples — автотесты code snippets из `dagstack/config-docs`

Каждый файл `test_<page>.py` содержит **дословно извлечённые**
Python-примеры из соответствующей страницы
[dagstack/config-docs](https://github.com/dagstack/config-docs) +
минимальные assertions. Цель — ловить drift между snippets в
документации и реальным API биндинга.

## Правила синхронизации

- Одна страница docs = один `test_<slug>.py`. Slug совпадает с MDX-
  файлом: `docs/intro.mdx` → `test_intro.py`,
  `docs/guides/testing.mdx` → `test_guides_testing.py`.
- Тело каждого теста **начинается** с кода из snippet'а **1-в-1**
  (комментарии, отступы — тоже). Дальше идут `assert`-проверки,
  воспроизводящие комментарии-ожидания (`# "order-service"`).
- Fixture-YAML embedded в тесте через `tmp_path` + `write_text()` —
  так тест self-contained, без зависимости от корневого
  `app-config.yaml` репо.
- Если snippet требует env-переменных (`${DB_USER}` и т.п.) —
  выставляем через `monkeypatch.setenv` в том же тесте.

## Почему в биндинге, а не в docs-репо

- Use существующую pytest-инфру: одна команда `pytest` запускает и
  unit-тесты API, и docs-примеры.
- Breaking change в API сразу видно как **красный тест с именем
  страницы docs** — автор знает что именно переписывать.
- Не нужно ставить `dagstack-config` отдельно для docs-тестов —
  тесты живут внутри пакета и используют текущий `src/`.

## Cross-reference в docs

В docs-репо (`config-docs`) каждая страница должна иметь блок "См. также"
с ссылкой на соответствующий test-файл —
`tests/docs_examples/test_<slug>.py` в репо биндинга. Это замкнутый
круг: docs ссылается на тест, тест копирует код docs.
