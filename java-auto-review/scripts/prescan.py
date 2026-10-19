#!/usr/bin/env python3
"""
Предпроверка автотеста (Spock/Groovy, а также Java/JUnit) по чеклисту.

Находит КАНДИДАТОВ в нарушения и точные номера строк.
Сам отклоняет кандидатов по проверяемым правилам и печатает ГОТОВЫЙ ответ.
Модели остаётся показать вывод без изменений.

Использование:
    python prescan.py LoginSpec.groovy [OtherSpec.groovy ...] [--numbered]

--numbered  дополнительно печатает код файла с номерами строк.
"""
import bisect
import re
import sys


# ---------- подготовка текста ----------

SLASHY_PREV = set("=~(,:[!&|?{;")


def _slashy_start(src, i):
    """Похоже ли '/' в позиции i на начало Groovy slashy-строки /regex/."""
    j = i - 1
    while j >= 0 and src[j] in " \t":
        j -= 1
    if j < 0 or src[j] in SLASHY_PREV:
        return True
    return src[max(0, j - 5):j + 1] == "return"


def strip_source(src):
    """Две копии исходника той же длины, переводы строк сохранены:
    no_c  - комментарии заменены пробелами;
    no_cs - дополнительно содержимое всех строк заменено пробелами.
    Понимает Groovy: '...', "...", '''...''', \"\"\"...\"\"\", /slashy/.
    """
    c, s = [], []
    i, n = 0, len(src)
    state = None
    end_tok = ""
    while i < n:
        ch = src[i]
        nx = src[i + 1] if i + 1 < n else ""
        if state is None:
            if ch == "/" and nx == "/":
                state = "line"; c += "  "; s += "  "; i += 2; continue
            if ch == "/" and nx == "*":
                state = "block"; c += "  "; s += "  "; i += 2; continue
            for tok in ('"""', "'''"):
                if src.startswith(tok, i):
                    state, end_tok = "str", tok
                    break
            if state == "str":
                c += end_tok; s += end_tok; i += 3; continue
            if ch in "\"'":
                state, end_tok = "str", ch
                c.append(ch); s.append(ch); i += 1; continue
            if ch == "/" and _slashy_start(src, i):
                state, end_tok = "str", "/"
                c.append(ch); s.append(ch); i += 1; continue
            c.append(ch); s.append(ch); i += 1; continue
        if state == "line":
            if ch == "\n":
                state = None; c.append("\n"); s.append("\n")
            else:
                c.append(" "); s.append(" ")
            i += 1; continue
        if state == "block":
            if ch == "*" and nx == "/":
                state = None; c += "  "; s += "  "; i += 2; continue
            c.append("\n" if ch == "\n" else " "); s.append("\n" if ch == "\n" else " ")
            i += 1; continue
        # state == "str"
        if ch == "\\" and i + 1 < n:
            c += src[i:i + 2]; s += "  "; i += 2; continue
        if src.startswith(end_tok, i):
            state = None; c += end_tok; s += end_tok; i += len(end_tok); continue
        if ch == "\n" and len(end_tok) == 1:  # незакрытая однострочная строка
            state = None; c.append("\n"); s.append("\n"); i += 1; continue
        c.append(ch); s.append("\n" if ch == "\n" else " "); i += 1
    return "".join(c), "".join(s)


def match_brace(text, open_idx):
    depth = 0
    for j in range(open_idx, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(text) - 1


# ---------- шаблоны ----------

Q = r"""["']"""
SLEEP_RE = re.compile(
    r"\bThread\s*\.\s*sleep\b|\bTimeUnit\s*\.\s*\w+\s*\.\s*sleep\b|(?<![\w.$])sleep\s*(?:\(|\d)"
)
STRING_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')
URL_IN_STRING_RE = re.compile(r"https?://|\blocalhost\b|\b\d{1,3}(?:\.\d{1,3}){3}\b")
CRED_WORD = r"(?:password|passwd|pwd|secret|token|api_?key|login|username)"
CREDS_RES = [
    re.compile(r"(?i)\b\w*" + CRED_WORD + r"\w*\s*[=:]\s*" + Q + r"[^\"'\n]+" + Q),
    re.compile(r"(?i)\.(?:set|with)?(?:password|passwd|pwd|pass|token|secret|apikey|login|username|user)\s*\(\s*" + Q + r"[^\"'\n]+" + Q),
    re.compile(r"(?i)\.basic\s*\(\s*" + Q + r"[^\"'\n]*" + Q + r"\s*,\s*" + Q + r"[^\"'\n]+" + Q),
    re.compile(Q + r"(?:Basic|Bearer)\s+[A-Za-z0-9._\-+/=]{8,}" + Q),
    re.compile(r"(?i)(?:pass|pwd|secret|token).*sendKeys\s*\(\s*" + Q + r"[^\"'\n]+" + Q),
    re.compile(r"(?i)" + Q + r"(?:password|passwd|pwd|pass|token|secret|api_?key|login|username)" + Q + r"\s*,\s*" + Q + r"[^\"'\n]+" + Q),
    re.compile(r"(?i)\b\w*(?:password|pwd|secret|token)\w*\s*<<\s*\[[^\]\n]*" + Q + r"[^\"'\n]+" + Q),
]
DRIVER_TYPES = r"(?:WebDriver|RemoteWebDriver|ChromeDriver|FirefoxDriver|EdgeDriver|SafariDriver|EventFiringWebDriver|Browser)"
STATIC_DRIVER_RES = [
    re.compile(r"\bstatic\b[^;=(){}\n]*\b" + DRIVER_TYPES + r"\s+\w+"),
    re.compile(r"\bstatic\s+(?:final\s+)?(?:def\s+)?(?:driver|webDriver|browser)\b"),
]
ORDER_RE = re.compile(r"@(?:Stepwise|FixMethodOrder|TestMethodOrder|Order)\b|\bdependsOnMethods\b|\bdependsOnGroups\b|\bpriority\s*=")
FIELD_RE = re.compile(
    r"^(?P<ann>@Shared\s+)?(?P<mods>(?:(?:private|protected|public|static|final|transient|volatile)\s+)*)"
    r"(?:def\s+|[\w.]+(?:<[^=]*>)?(?:\[\])*\s+)?(?P<name>[A-Za-z_]\w*)\s*(?:=.*)?;?\s*$"
)
METHOD_RE = re.compile(
    r"^[ \t]*(?:@\w+(?:\([^)\n]*\))?\s+)*"
    r"(?P<prefix>(?:(?:[\w.]+(?:<[^>\n]*>)?(?:\[\])*)\s+)+)"
    r"(?P<name>\"[^\"\n]*\"|'[^'\n]*'|\w+)\s*\([^)]*\)\s*(?:throws[^{]*)?\{",
    re.M,
)
NOT_METHOD = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new", "else", "try"}
FIXTURES = {"setup", "cleanup", "setupSpec", "cleanupSpec"}
LABEL_RE = re.compile(r"^[ \t]*(given|setup|when|then|expect|where|cleanup|and)\s*:", re.M)
TEST_ANN_RE = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest)\b(\s*\([^)]*\))?")
FIXTURE_ANN_RE = re.compile(r"@(?:Before|After)\w*\b")
JUNIT_CHECK_RE = re.compile(
    r"\b(?:assert\w*|verify\w*|should\w*|expect\w*|check\w*|validate\w*|fail|then|statusCode|body)\s*\("
    r"|\bassert\b|\bsoftly\s*\.|\bSoftAssertions\b"
)
CLOSURE_IN_THEN_RE = re.compile(r"\.(?:each|eachWithIndex|forEach|with)\s*\{")
CATCH_RE = re.compile(r"\bcatch\s*\(([^)]*)\)\s*\{")
RETHROW_RE = re.compile(r"\bthrow\b|\bfail\s*\(")


LOOP_RE = re.compile(r"\b(?:for|while)\s*\([^)]*\)\s*\{|\.times\s*\{|\bdo\s*\{|\.upto\s*\([^)]*\)\s*\{")
NEG_RE = re.compile(r"(?i)wrong|invalid|incorrect|bad|fake|unknown|expired|неверн|ошибоч|чуж")
NS_RE = re.compile(r"(?i)w3\.org|xmlns|schemas\.|apache\.org/xml|purl\.org")
LABEL_LINE_RE = re.compile(r"^(?:given|setup|when|then|expect|where|cleanup|and)\s*:")
ASSIGN_LINE_RE = re.compile(r"^(?:def\s+|var\s+|final\s+)?(?:[\w<>\[\]]+\s+)?(\w+)\s*=(?!=)")


def env_for(text):
    t = text.lower()
    if re.search(r"pass|pwd", t):
        return "TEST_PASSWORD"
    if re.search(r"token|secret|api_?key|bearer|basic", t):
        return "API_TOKEN"
    if re.search(r"login|user", t):
        return "TEST_USER"
    return "TEST_SECRET"


def plural(n):
    if n % 10 == 1 and n % 100 != 11:
        return "нарушение"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "нарушения"
    return "нарушений"


def scan(src, is_java):
    no_c, no_cs = strip_source(src)
    starts = [0] + [m.end() for m in re.finditer(r"\n", src)]
    lines = src.split("\n")

    def line_of(idx):
        return bisect.bisect_right(starts, idx)

    def code(ln):
        return lines[ln - 1].strip()

    found = {}

    def add(rule, idx, problem, fix="", fix_code=None, hint=None, reject=None):
        key = (rule, line_of(idx))
        if key not in found:
            found[key] = dict(rule=rule, line=key[1], problem=problem, fix=fix,
                              code=fix_code, hint=hint, reject=reject)

    def replace_in_line(ln, spans_repl):
        """spans_repl: [(abs_start, abs_end, new_text)] -> изменённая строка (strip)."""
        text = lines[ln - 1]
        base = starts[ln - 1]
        for a, b, new in sorted(spans_repl, reverse=True):
            text = text[:a - base] + new + text[b - base:]
        return text.strip()

    # ---------- методы ----------
    methods = []
    for m in METHOD_RE.finditer(no_cs):
        if re.search(r"\b(?:new|return|else)\b", m.group("prefix")):
            continue
        raw_name = no_c[m.start("name"):m.end("name")]
        if raw_name in NOT_METHOD:
            continue
        brace = m.end() - 1
        end = match_brace(no_cs, brace)
        before = no_cs[max(0, m.start() - 300):m.start()]
        before = before[max(before.rfind("}"), before.rfind(";")) + 1:]
        methods.append({
            "name": raw_name, "idx": m.start("name"), "start": brace, "end": end,
            "body": no_cs[brace + 1:end], "body_c": no_c[brace + 1:end],
            "ann": before + no_cs[m.start():m.start("prefix")],
        })
    for mt in methods:
        labels = LABEL_RE.findall(mt["body"])
        bare = mt["name"].strip("\"'")
        if bare in FIXTURES or FIXTURE_ANN_RE.search(mt["ann"]):
            mt["kind"] = "fixture"
        elif TEST_ANN_RE.search(mt["ann"]):
            mt["kind"] = "junit"
        elif mt["name"][0] in "\"'" or labels:
            mt["kind"] = "feature"
        else:
            mt["kind"] = "helper"
        mt["labels"] = labels

    def title(mt):
        return mt["name"] if mt["name"][0] in "\"'" else mt["name"] + "()"

    def enclosing(idx):
        best = None
        for mt in methods:
            if mt["start"] < idx < mt["end"] and (best is None or mt["start"] > best["start"]):
                best = mt
        return best

    def next_code_line(ln, stop_ln):
        for k in range(ln, min(stop_ln, len(lines))):
            t = lines[k].strip()
            if not t or t in ("try {", "{", "}") or LABEL_LINE_RE.match(t) or t.startswith("//") or t.startswith("} catch"):
                continue
            return k + 1, t.rstrip("{").strip()
        return None, None

    def last_code_line(mt):
        for k in range(line_of(mt["end"]) - 1, line_of(mt["start"]), -1):
            t = lines[k - 1].strip()
            if not t or t == "}" or LABEL_LINE_RE.match(t) or t.startswith("//"):
                continue
            return k, t
        k = line_of(mt["start"])
        inner = mt["body_c"].strip().split("\n")[0].strip()
        return k, inner

    tests = [mt for mt in methods if mt["kind"] in ("feature", "junit")]
    loops = [(m.start(), match_brace(no_cs, m.end() - 1)) for m in LOOP_RE.finditer(no_cs)]

    # ---------- SLEEP ----------
    for m in SLEEP_RE.finditer(no_cs):
        ln = line_of(m.start())
        reject = None
        for a, b in loops:
            if a < m.start() < b and re.search(r"\b(?:if|break|return)\b", no_cs[a:b]):
                reject = "sleep в цикле опроса"
        line = code(ln)
        dur = ""
        tu = re.search(r"TimeUnit\s*\.\s*(\w+)\s*\.\s*sleep\s*\(\s*(\d+)", line)
        ms = re.search(r"sleep\s*\(?\s*(\d+)", line)
        if tu:
            dur = f" {tu.group(2)} {tu.group(1).lower()}"
        elif ms:
            dur = f" {int(ms.group(1)) / 1000:g} с"
        enc = enclosing(m.start())
        nln, ntext = next_code_line(ln, line_of(enc["end"]) if enc else len(lines))
        elem = None
        if ntext:
            t2 = re.sub(r"^(?:def\s+|var\s+)?\w+\s*=(?!=)\s*", "", ntext)
            em = re.match(r"^(.*?)\s*\.\s*(?:click|sendKeys|clear|submit|getText|text|value|getAttribute|isDisplayed|displayed|shouldBe|shouldHave)\b", t2)
            if em and ("findElement" in em.group(1) or "$" in em.group(1) or "." in em.group(1)):
                elem = em.group(1).strip()
        if elem and elem.startswith("$"):
            fx = f"`{elem}.shouldBe(visible, Duration.ofSeconds(10))`"
        elif elem and is_java:
            fx = f"`new WebDriverWait(driver, Duration.ofSeconds(10)).until(d -> {elem}.isDisplayed())`"
        elif elem:
            fx = f"`new PollingConditions(timeout: 10).eventually {{ assert {elem}.displayed }}`"
        elif is_java:
            fx = f"заменить на `WebDriverWait` с условием, которого ждёт строка {nln or ln + 1}"
        else:
            fx = f"заменить на `new PollingConditions(timeout: 10).eventually {{ assert ... }}` с условием, которого ждёт строка {nln or ln + 1}"
        hint = None
        add("SLEEP", m.start(), f"Пауза{dur} вместо ожидания условия", fx, reject=reject)

    # ---------- HARDCODE_URL ----------
    for m in STRING_RE.finditer(no_c):
        lit = m.group(0)
        if not URL_IN_STRING_RE.search(lit):
            continue
        ln = line_of(m.start())
        line = code(ln)
        reject = None
        if re.search(r"\b(?:getProperty|getenv)\s*\(", line):
            reject = "URL по умолчанию в getProperty"
        elif NS_RE.search(lit):
            reject = "не адрес стенда"
        q, body = lit[0], lit[1:-1]
        um = re.match(r"^(?:https?://[^/]+|localhost(?::\d+)?|\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?)(.*)$", body)
        path = um.group(1) if um else ""
        repl = "System.getProperty('baseUrl')" if not is_java else 'System.getProperty("baseUrl")'
        if path:
            repl += f" + {q}{path}{q}"
        new_line = replace_in_line(ln, [(m.start(), m.end(), repl)])
        add("HARDCODE_URL", m.start(), "Адрес стенда зашит в код", f"`{new_line}`", fix_code=new_line, reject=reject)

    # ---------- HARDCODE_CREDS ----------
    def getenv(name):
        return f"System.getenv('{name}')" if not is_java else f'System.getenv("{name}")'

    for k, rx in enumerate(CREDS_RES):
        for m in rx.finditer(no_c):
            ln = line_of(m.start())
            if ("HARDCODE_CREDS", ln) in found:
                continue
            ls = starts[ln - 1]
            le = no_c.find("\n", m.end())
            le = len(no_c) if le == -1 else le
            lits = [(x.start(), x.end()) for x in STRING_RE.finditer(no_c, ls, le)
                    if x.start() >= m.start() and x.end() <= m.end()]
            if not lits:
                continue
            text = m.group(0)
            if k == 2:      # .basic('user', 'pass')
                vals, envs = lits[-2:], ["TEST_USER", "TEST_PASSWORD"]
                problem = "Логин и пароль зашиты в код"
            elif k == 3:    # 'Bearer xxx'
                vals, envs = lits[-1:], ["API_TOKEN"]
                problem = "Токен зашит в код"
            elif k == 6:    # password << ['a', 'b']
                p = m.start() + text.index("<<")
                vals = [s for s in lits if s[0] > p]
                envs = [env_for(text[:text.index("<<")])] * len(vals)
                problem = "Пароли в data pipe зашиты в код"
            else:
                vals = lits[-1:]
                envs = [env_for(no_c[m.start():vals[0][0]])]
                problem = {"TEST_PASSWORD": "Пароль зашит в код", "API_TOKEN": "Токен зашит в код",
                           "TEST_USER": "Логин зашит в код"}.get(envs[0], "Секрет зашит в код")
            contents = [no_c[a + 1:b - 1] for a, b in vals]
            reject = "негативные данные" if all(c == "" or NEG_RE.search(c) for c in contents) else None
            repl = []
            for (a, b), env in zip(vals, envs):
                if k == 3:
                    q = no_c[a]
                    word = re.match(r"\W(\w+)", no_c[a:b]).group(1)
                    repl.append((a, b, f"{q}{word} {q} + {getenv(env)}"))
                else:
                    repl.append((a, b, getenv(env)))
            new_line = replace_in_line(ln, repl)
            add("HARDCODE_CREDS", m.start(), problem, f"`{new_line}`", fix_code=new_line, reject=reject)

    for mt in tests:   # таблицы where:
        lm = [x for x in LABEL_RE.finditer(mt["body"]) if x.group(1) == "where"]
        if not lm:
            continue
        w_start = mt["start"] + 1 + lm[0].end()
        header, cols, pos = None, [], w_start
        for raw in no_c[w_start:mt["end"]].split("\n"):
            t = raw.strip()
            if "|" in t:
                cells = [x.strip() for x in re.split(r"\|\|?", t)]
                if header is None:
                    header = cells
                    cols = [i for i, x in enumerate(cells) if re.search(r"(?i)pass|pwd|secret|token|api_?key", x)]
                else:
                    for i in cols:
                        if i < len(cells) and re.fullmatch(r"([\"']).*\1", cells[i]) and len(cells[i]) >= 2:
                            a = pos + raw.find(cells[i])
                            b = a + len(cells[i])
                            val = cells[i][1:-1]
                            reject = "негативные данные" if val == "" or NEG_RE.search(val) else None
                            new_line = replace_in_line(line_of(a), [(a, b, getenv(env_for(header[i])))])
                            add("HARDCODE_CREDS", a, f"Пароль в таблице where: (колонка {header[i]}) зашит в код",
                                f"`{new_line}`", fix_code=new_line, reject=reject)
                            break
            pos += len(raw) + 1

    # ---------- STATIC_DRIVER ----------
    for rx in STATIC_DRIVER_RES:
        for m in rx.finditer(no_cs):
            ln = line_of(m.start())
            line = code(ln)
            if "ThreadLocal" in line:
                continue
            decl = re.sub(r"\bstatic\s+", "", line).rstrip(";")
            var = re.findall(r"(\w+)\s*(?:=|;|$)", decl.split("=")[0] + ";")
            var = var[-1] if var else "driver"
            if is_java:
                fx = f"`{decl.split('=')[0].strip()};` (без static) + `@BeforeEach void setUp() {{ {var} = new ChromeDriver(); }}`"
            else:
                fx = f"`{decl.split('=')[0].strip()}` (без static) + `def setup() {{ {var} = new ChromeDriver() }}`"
            add("STATIC_DRIVER", m.start(), "Один браузер на все тесты при параллельном запуске", fx)

    # ---------- TEST_DEPENDENCY: порядок ----------
    first_order = True
    for m in ORDER_RE.finditer(no_cs):
        tok = m.group(0)
        reject = None if first_order else "дубль порядка тестов"
        first_order = False
        if "Stepwise" in tok:
            add("TEST_DEPENDENCY", m.start(), "@Stepwise: тесты выполняются цепочкой и зависят друг от друга",
                "убрать `@Stepwise`, данные для каждого теста готовить в `setup()`", reject=reject)
        else:
            add("TEST_DEPENDENCY", m.start(), "Задан порядок выполнения тестов",
                f"убрать `{tok.strip()}`, каждый тест готовит свои данные сам", reject=reject)

    # ---------- TEST_DEPENDENCY: @Shared/static поля ----------
    depth, prev = 0, ""
    for ln, text in enumerate(no_cs.split("\n"), 1):
        t = text.strip()
        if depth == 1 and t:
            head = t.split("=", 1)[0]
            fm = FIELD_RE.match(t)
            if fm and "(" not in head and "{" not in head and (not t.startswith("@") or fm.group("ann")):
                shared = bool(fm.group("ann")) or prev == "@Shared"
                mods, name = fm.group("mods"), fm.group("name")
                if (shared or "static" in mods) and "final" not in mods \
                        and not re.search(r"ThreadLocal|Logger|\bLog\b|" + DRIVER_TYPES, t) and name not in NOT_METHOD:
                    assign = re.compile(
                        r"(?<![\w.])" + name + r"\s*(?:=(?!=)|<<|\+=|-=|\+\+|--)"
                        r"|(?<![\w.])" + name + r"\s*\.\s*(?:add|addAll|put|putAll|remove|clear|push|leftShift)\s*\("
                        r"|(?<![\w.])" + name + r"\s*\[[^\]]*\]\s*=(?!=)"
                    )
                    writers = []
                    for mt in tests:
                        am = assign.search(mt["body"])
                        if am:
                            writers.append((mt, line_of(mt["start"] + 1 + am.start())))
                    readers = [title(mt) for mt in tests if mt not in [w[0] for w in writers]
                               and re.search(r"(?<![\w.])" + name + r"\b", mt["body_c"])]
                    if writers:
                        wmt, wln = writers[0]
                        if readers:
                            problem = f"{', '.join(readers)} {'читает' if len(readers) == 1 else 'читают'} {name} из {title(wmt)}"
                        else:
                            problem = f"{name} меняется в {title(wmt)} и сохраняется между тестами"
                        kw = "@Shared" if shared else "static"
                        wline = code(wln).rstrip(";")
                        rm = re.search(r"(?<![\w.])" + name + r"\s*=(?!=)\s*(.+)$", wline)
                        rhs = rm.group(1).split(";")[0].strip() if rm else None
                        while rhs and rhs.endswith("}") and rhs.count("}") > rhs.count("{"):
                            rhs = rhs[:-1].strip()
                        local_names = set(re.findall(r"\bdef\s+(\w+)", wmt["body"])) | set(
                            re.findall(r"\b[A-Z]\w*(?:<[^>]*>)?\s+(\w+)\s*=", wmt["body"]))
                        used = set(re.findall(r"(?<![\w.'\"])([a-z_]\w*)", rhs or ""))
                        base_name = re.sub(r"(?:Id|ID|Ids|List|s)$", "", name) or name
                        helper = "create" + base_name[0].upper() + base_name[1:] + "()"
                        note = ""
                        if not rhs or used & local_names or "|" in rhs:
                            rhs = helper
                            note = f", код для {helper} взять из {title(wmt)}"
                        if is_java:
                            fx = f"`@BeforeEach void setUp() {{ {name} = {rhs}; }}` (поле без {kw}{note})"
                        else:
                            fx = f"`def setup() {{ {name} = {rhs} }}` (поле без {kw}{note})"
                        hint = None
                        add("TEST_DEPENDENCY", starts[ln - 1] + len(text) - len(text.lstrip()), problem, fx, hint=hint)
        if t:
            prev = t
        depth += text.count("{") - text.count("}")

    # ---------- NO_ASSERT ----------
    for mt in tests:
        if mt["kind"] == "junit":
            if re.search(r"\bexpected(?:Exceptions)?\s*=", mt["ann"]) or JUNIT_CHECK_RE.search(mt["body"]):
                continue
            lln, ltext = last_code_line(mt)
            add("NO_ASSERT", mt["idx"], f"Тест {title(mt)} ничего не проверяет",
                f"добавить `assertThat(...)` для результата строки {lln}: `{ltext}`")
            continue
        if "then" not in mt["labels"] and "expect" not in mt["labels"]:
            lln, ltext = last_code_line(mt)
            ltext = (ltext or "").rstrip(";")
            am = ASSIGN_LINE_RE.match(ltext)
            http = re.search(r"\.(?:get|post|put|patch|delete|request)\s*\(", ltext)
            if am and http:
                fx = f"добавить `then: {am.group(1)}.status in [200, 201, 204]`"
            elif am:
                fx = f"добавить `then:` с проверкой `{am.group(1)}` (строка {lln})"
            elif http:
                fx = f"в when: `def result = {ltext}`, добавить `then: result.status in [200, 201, 204]`"
            else:
                fx = f"добавить `then:` с проверкой результата строки {lln}: `{ltext}`"
            add("NO_ASSERT", mt["idx"], "Тест ничего не проверяет: нет блока then:/expect:", fx)
            continue
        body, base = mt["body"], mt["start"] + 1
        marks = [(x.group(1), x.start(), x.end()) for x in LABEL_RE.finditer(body)]
        for k, (lab, _, lend) in enumerate(marks):
            if lab not in ("then", "expect"):
                continue
            stop = len(body)
            for lab2, s2, _ in marks[k + 1:]:
                if lab2 != "and":
                    stop = s2
                    break
            for cm in CLOSURE_IN_THEN_RE.finditer(body, lend, stop):
                ob = base + cm.end() - 1
                if re.search(r"\bassert\b", no_cs[ob:match_brace(no_cs, ob)]):
                    continue
                ln = line_of(base + cm.start())
                line = code(ln)
                if ".with" in cm.group(0):
                    new_line = re.sub(r"([\w.()\[\]'\"$]+)\.with\s*\{", r"with(\1) {", line, count=1)
                    problem = "Условие внутри .with не проверяется, строка никогда не падает"
                else:
                    new_line = re.sub(r"\.(?:each|forEach)\s*\{", ".every {", line, count=1)
                    problem = "Условие внутри .each не проверяется, строка никогда не падает"
                if new_line == line:  # eachWithIndex и т.п.
                    add("NO_ASSERT", base + cm.start(), problem, "добавить `assert` перед условием внутри замыкания")
                else:
                    add("NO_ASSERT", base + cm.start(), problem, f"`{new_line}`", fix_code=new_line)

    # ---------- SWALLOWED_EXCEPTION ----------
    for m in CATCH_RE.finditer(no_cs):
        brace = m.end() - 1
        end = match_brace(no_cs, brace)
        body = no_cs[brace + 1:end].strip()
        body_c = no_c[brace + 1:end]
        if body and RETHROW_RE.search(body):
            continue
        enc = enclosing(m.start())
        reject = f"в {title(enc)}" if enc and enc["kind"] == "fixture" else None
        if not body:
            how = "глушится"
        elif "printStackTrace" in body_c:
            how = "печатается"
        else:
            how = "пишется в лог"
        try_ln = None
        for tm in re.finditer(r"\btry\s*\{", no_cs[:m.start()]):
            tend = match_brace(no_cs, tm.end() - 1)
            if not no_cs[tend + 1:m.start()].strip():
                try_ln = line_of(tm.start())
        rng = f" (строки {try_ln}–{line_of(end)})" if try_ln else ""
        add("SWALLOWED_EXCEPTION", m.start(), f"Исключение {how}, тест остаётся зелёным",
            f"убрать try/catch{rng}, оставить код из try", reject=reject)

    return sorted(found.values(), key=lambda c: (c["line"], c["rule"])), lines


def cell(text):
    return text.replace("|", "/").strip()


def report(path, numbered, multi):
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    cands, lines = scan(src, path.endswith(".java"))
    ok = [c for c in cands if not c["reject"]]
    rejected = [c for c in cands if c["reject"]]

    if multi:
        print(f"**{path.split('/')[-1]}**")
        print()
    if not ok:
        print("Нарушений по чеклисту не найдено.")
    else:
        print("| № | Пункт | Строка | Проблема | Исправление |")
        print("|---|-------|--------|----------|-------------|")
        blocks = []
        for n, c in enumerate(ok, 1):
            fix = c["fix"]
            if c["code"] and ("|" in c["code"] or len(c["code"]) > 110):
                fix = f"см. ниже №{n}"
                blocks.append((n, c["code"]))
            print(f"| {n} | {c['rule']} | {c['line']} | {cell(c['problem'])} | {fix} |")
        if blocks:
            print()
            print("### Код исправлений")
            for n, codetext in blocks:
                print(f"**№{n}**")
                print("```groovy")
                print(codetext)
                print("```")
        print()
        print(f"Итого: {len(ok)} {plural(len(ok))}.")
        if rejected:
            print("Отклонено: " + "; ".join(f"стр. {c['line']} — {c['reject']}" for c in rejected) + ".")
        else:
            print("Отклонено: нет.")
    if numbered:
        print()
        print("Код с номерами строк:")
        width = len(str(len(lines)))
        for i, line in enumerate(lines, 1):
            print(f"{i:>{width}}: {line}")
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    numbered = "--numbered" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    for path in args:
        report(path, numbered, len(args) > 1)


if __name__ == "__main__":
    main()
