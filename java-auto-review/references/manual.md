# Ручной режим (скрипт не запускается)

Проверяй код только по 7 пунктам ниже. Других замечаний не пиши.
В колонке «Строка» пиши название теста, например `"вход в систему"`, — сам номера строк не считай.

Напоминание: в Spock каждая строка в `then:` и `expect:` — это проверка. Внутри `.each { }`, `.with { }` и вспомогательных методов нужен `assert`.

## Чеклист

| Пункт | Что искать | Отклонить, если | Исправление |
|---|---|---|---|
| SLEEP | `sleep(3000)`, `sleep 3000`, `Thread.sleep`, `TimeUnit.X.sleep` | sleep внутри цикла с `if`/`break`/`return` | `new PollingConditions(timeout: 10).eventually { assert <что ждём> }` |
| NO_ASSERT | тест без `then:`/`expect:`; в `then:` стоит `.each { условие }` или `x.with { условие }` без `assert` | — | `then: result.status == 200`; `.each {` → `.every {`; `x.with {` → `with(x) {` |
| HARDCODE_URL | строка с `http://`, `https://`, `localhost`, IP | URL — второй аргумент `getProperty`/`getenv`; ссылка на стандарт или namespace | `System.getProperty('baseUrl') + '/путь'` |
| HARDCODE_CREDS | `password: '...'`, `.basic('u','p')`, `sendKeys('...')` в поле пароля, `'Bearer ...'`, пароль в таблице `where:` | значение пустое или явно неверное (`wrong`, `invalid`) | `System.getenv('TEST_PASSWORD')` |
| TEST_DEPENDENCY | `@Stepwise`, `@Order`; `@Shared`/`static` поле, которое один тест пишет, а другой читает | поле меняется только в `setupSpec()` | убрать `@Stepwise`; `def setup() { поле = создатьДанные() }`, поле без `@Shared` |
| STATIC_DRIVER | `static WebDriver`, `static Browser`, `static driver` | это `ThreadLocal` | `WebDriver driver` + `def setup() { driver = new ChromeDriver() }` |
| SWALLOWED_EXCEPTION | `catch` пустой, только `printStackTrace()` или только лог | catch в `cleanup()`/`cleanupSpec()`/`@After...` | убрать try/catch; если исключение ожидается — `then: thrown(ИмяИсключения)` |

## Формат ответа

~~~
| № | Пункт | Строка | Проблема | Исправление |
|---|-------|--------|----------|-------------|
| 1 | SLEEP | "вход в систему" | Пауза 3 с вместо ожидания условия | `new PollingConditions(timeout: 10).eventually { assert profile.displayed }` |

Итого: 1 нарушение.
Отклонено: нет.
~~~

- Одна строка = одно нарушение. «Проблема» — до 12 слов. «Исправление» — код в обратных кавычках.
- Символ `|` в ячейках запрещён. Длинный код или код с `|` вынеси под таблицу в блок `### Код исправлений` и напиши в ячейке «см. ниже №N».
- «Отклонено:» — что отклонено и почему (2–4 слова) или «нет».
- Нет нарушений — одна строка: `Нарушений по чеклисту не найдено.`
