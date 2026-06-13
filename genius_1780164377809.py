import asyncio
import aiohttp
import json
import time
import os
import random
import unicodedata
from datetime import date

BASE_URL = "https://www.indiageniuschallenge.com/api"
CACHE_FILE = "answers_cache.json"
PROBE_STATS_FILE = "probe_stats.json"
QUIZ_KEY = f"daily_{date.today().isoformat()}"

# ========== BROWSER PROFILES ==========
_BROWSER_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Accept-Language": "en-US,en;q=0.9",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Accept-Language": "en-GB,en;q=0.9",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "sec-ch-ua": None,
        "sec-ch-ua-mobile": None,
        "sec-ch-ua-platform": None,
        "Accept-Language": "en-US,en;q=0.5",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "sec-ch-ua": None,
        "sec-ch-ua-mobile": None,
        "sec-ch-ua-platform": None,
        "Accept-Language": "en-IN,en;q=0.9",
    },
    {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "Accept-Language": "en-IN,hi;q=0.8,en;q=0.6",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "sec-ch-ua": '"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Accept-Language": "en-US,en;q=0.9",
    },
]

_ACCEPT_ENCODINGS = ["gzip, deflate, br", "gzip, deflate, br, zstd", "gzip, deflate"]
_SEC_FETCH_SITES = ["same-origin", "same-site"]

def get_headers() -> dict:
    profile = random.choice(_BROWSER_PROFILES)
    headers = {
        "User-Agent": profile["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": random.choice(_ACCEPT_ENCODINGS),
        "Accept-Language": profile["Accept-Language"],
        "Content-Type": "application/json",
        "Origin": "https://www.indiageniuschallenge.com",
        "Referer": "https://www.indiageniuschallenge.com/quiz",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": random.choice(_SEC_FETCH_SITES),
        "Connection": "keep-alive",
    }
    for key in ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"):
        value = profile.get(key)
        if value is not None:
            headers[key] = value
    return headers

HEADERS = get_headers()

def normalize_answer(value) -> str:
    s = str(value)
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.strip()
    s = " ".join(s.split())
    return s.lower()

def answers_match(a, b) -> bool:
    return normalize_answer(a) == normalize_answer(b)

def load_cookies(file_path):
    with open(file_path, 'r') as f:
        cookie_list = json.load(f)
    return {c['name']: c['value'] for c in cookie_list if 'name' in c and 'value' in c}

def save_cookies(file_path, raw_text):
    data = json.loads(raw_text)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  ✅ Cookies saved to {file_path}")

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            return cache
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

def load_probe_stats():
    if os.path.exists(PROBE_STATS_FILE):
        with open(PROBE_STATS_FILE, 'r') as f:
            return json.load(f)
    return {"questions": {}}

def save_probe_stats(stats):
    with open(PROBE_STATS_FILE, 'w') as f:
        json.dump(stats, f, indent=2)

def merged_quiz_cache(cache: dict) -> dict:
    merged = {}
    for key in sorted(k for k in cache if k.startswith("daily_") and k != QUIZ_KEY):
        if isinstance(cache[key], dict):
            merged.update(cache[key])
    merged.update(cache.get(QUIZ_KEY, {}))
    return merged

# ========== API HELPERS ==========
async def generate_attempt(session, cookies=None):
    if cookies is None:
        cookies = {}
    print(f"  [API] generate_attempt: starting...")
    try:
        async with session.post(
            f"{BASE_URL}/attempt/generate",
            headers=get_headers(),
            cookies=cookies,
            json={},
        ) as resp:
            print(f"  [API] generate_attempt response status: {resp.status}")
            anon_cookie = None
            for morsel in resp.cookies.values():
                if morsel.key == "anon_attempt_id":
                    anon_cookie = morsel.value
                    print(f"  [API] got anon_attempt_id: {anon_cookie[:16]}...")
                    break
            data = await resp.json(content_type=None)
            if not data or not data.get("success") or not data.get("data"):
                print(f"  [API] generate_attempt: success false or missing data")
                return None, None, None
            quiz = data["data"]["quiz"]
            attempt = data["data"]["attempt"]
            print(f"  [API] generated attempt_id: {attempt['_id'][:16]}...")
            return attempt["_id"], quiz["Questions"], anon_cookie
    except Exception as e:
        print(f"  [API] generate_attempt exception: {e}")
        return None, None, None

async def validate_answer(session, cookies, attempt_id, question,
                          selected_answer, time_spent, total_time_used=None,
                          retries=3):
    payload = {
        "_id": attempt_id,
        "questionId": question["_id"],
        "question": question.get("question") or "",
        "selectedAnswer": selected_answer,
        "timeSpent": time_spent,
    }
    if total_time_used is not None:
        payload["totalTimeUsed"] = total_time_used
    for attempt_num in range(retries):
        try:
            async with session.post(
                f"{BASE_URL}/attempt/validate",
                headers=get_headers(),
                cookies=cookies,
                json=payload,
            ) as resp:
                if resp.status == 429:
                    wait = min(30, 2 ** attempt_num + random.uniform(0.5, 2.0))
                    print(f"  [API] validate_answer: rate limit, waiting {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                data = await resp.json(content_type=None)
            if not data:
                await asyncio.sleep(1)
                continue
            if data.get("duplicateSubmission"):
                print(f"  [API] validate_answer: duplicate submission")
                return None
            for entry in data.get("data", {}).get("QuestionsAttempted", []):
                if entry.get("questionId") == question["_id"]:
                    is_correct = entry.get("isCorrect", False)
                    print(f"  [API] validate_answer: question {question['_id'][:8]}... correct={is_correct}")
                    return is_correct
            print(f"  [API] validate_answer: no entry found")
            return False
        except Exception as e:
            print(f"  [API] validate_answer exception: {e}")
            await asyncio.sleep(2)
    return False

async def fetch_attempt_details(session, attempt_id):
    print(f"  [API] fetch_attempt_details: fetching {attempt_id[:16]}...")
    try:
        cookies = {"anon_attempt_id": attempt_id}
        async with session.get(
            f"{BASE_URL}/attempt/{attempt_id}",
            headers=get_headers(),
            cookies=cookies,
        ) as resp:
            print(f"  [API] fetch_attempt_details status: {resp.status}")
            data = await resp.json(content_type=None)
            if data and data.get("success"):
                inner = data.get("data", data)
                questions = inner.get("questions", [])
                print(f"  [API] fetch_attempt_details: got {len(questions)} questions")
                return questions
            else:
                print(f"  [API] fetch_attempt_details: success false")
                return []
    except Exception as e:
        print(f"  [API] fetch_attempt_details exception: {e}")
        return []

async def update_cache_from_attempt(session, attempt_id, quiz_cache, probe_stats):
    print(f"  [CACHE] update_cache_from_attempt: learning from {attempt_id[:16]}...")
    correct_data = await fetch_attempt_details(session, attempt_id)
    newly_learned = 0
    for q in correct_data:
        qid = q.get("_id")
        correct_answer = q.get("answer")
        if not qid or not correct_answer:
            continue
        if qid not in probe_stats["questions"]:
            probe_stats["questions"][qid] = {"appearances": 0, "cached": False, "correct_answer": None}
        probe_stats["questions"][qid]["correct_answer"] = correct_answer
        if qid not in quiz_cache or not answers_match(quiz_cache[qid], correct_answer):
            quiz_cache[qid] = correct_answer
            newly_learned += 1
            probe_stats["questions"][qid]["cached"] = True
            print(f"  [CACHE] learned new answer for {qid[:12]}...: {correct_answer[:40]}")
        else:
            probe_stats["questions"][qid]["cached"] = True
    if newly_learned > 0:
        print(f"  [CACHE] learned {newly_learned} new answers, saving cache")
        cache = load_cache()
        cache[QUIZ_KEY] = quiz_cache
        save_cache(cache)
        save_probe_stats(probe_stats)
    return newly_learned

# ========== PROBE ATTEMPT ==========
async def run_probe_attempt(session, quiz_cache, question_meta, tried_options, run_num, probe_stats):
    print(f"\n  [PROBE {run_num}] Starting probe...")
    attempt_id, questions, _ = await generate_attempt(session)
    if not attempt_id:
        print(f"  [PROBE {run_num}] Failed to generate attempt")
        return 0
    print(f"  [PROBE {run_num}] Attempt ID: {attempt_id[:16]}...")
    for q in questions:
        qid = q["_id"]
        if qid not in probe_stats["questions"]:
            probe_stats["questions"][qid] = {"appearances": 0, "cached": False, "correct_answer": None}
        probe_stats["questions"][qid]["appearances"] += 1
    selections = {}
    for q in questions:
        qid = q["_id"]
        if qid in quiz_cache:
            selections[qid] = quiz_cache[qid]
        else:
            if qid not in tried_options:
                tried_options[qid] = {"tried_contents": [], "tried_indices": []}
            tried_contents_norm = {normalize_answer(c) for c in tried_options[qid].get("tried_contents", [])}
            untried_options = [opt for opt in q["options"] if normalize_answer(opt) not in tried_contents_norm]
            if not untried_options:
                tried_options[qid] = {"tried_contents": [], "tried_indices": []}
                untried_options = q["options"]
            opt_content = untried_options[0]
            tried_options[qid]["tried_contents"].append(opt_content)
            selections[qid] = opt_content
    total_time = 0
    for i, q in enumerate(questions):
        is_last = (i == len(questions) - 1)
        if q["_id"] not in question_meta:
            question_meta[q["_id"]] = {
                "question": q.get("question", ""),
                "options": q.get("options", []),
                "category": q.get("subCategory", ""),
                "difficulty": q.get("difficulty", ""),
                "type": q.get("type", "text"),
            }
        option = selections[q["_id"]]
        t = round(random.uniform(1.6, 2.2), 2)
        total_time += t
        await validate_answer(
            session, {}, attempt_id, q, option, t,
            total_time_used=round(total_time, 2) if is_last else None,
        )
        await asyncio.sleep(random.uniform(0.2, 0.4))
    new_learned = await update_cache_from_attempt(session, attempt_id, quiz_cache, probe_stats)
    print(f"  [PROBE {run_num}] Done. Learned {new_learned} new answers. Total time: {total_time:.1f}s\n")
    return new_learned

async def collect_answers(session, num_runs=30, concurrency=1):
    cache = load_cache()
    quiz_cache = merged_quiz_cache(cache)
    question_meta = dict(cache.get("question_meta", {}))
    tried_options = {}
    probe_stats = load_probe_stats()
    total_learned = 0
    for i in range(1, num_runs + 1):
        learned = await run_probe_attempt(session, quiz_cache, question_meta, tried_options, i, probe_stats)
        total_learned += learned
        print(f"  Probe {i}/{num_runs} done | Cache: {len(quiz_cache)} | Learned: {total_learned}")
        await asyncio.sleep(random.uniform(3, 6))
    cache[QUIZ_KEY] = quiz_cache
    cache["question_meta"] = question_meta
    cache["tried_options"] = tried_options
    save_cache(cache)
    save_probe_stats(probe_stats)
    return quiz_cache, question_meta

# ========== SATURATION ==========
async def is_cache_saturated(session, quiz_cache, question_meta, max_probes=200, required_no_new=10):
    tried_options = {}
    probe_stats = load_probe_stats()
    no_new_count = 0
    for i in range(1, max_probes + 1):
        before = len(quiz_cache)
        await run_probe_attempt(session, quiz_cache, question_meta, tried_options, i, probe_stats)
        after = len(quiz_cache)
        if after > before:
            print(f"  Probe {i}: +{after-before} new answers, cache now {after}")
            no_new_count = 0
        else:
            no_new_count += 1
            if no_new_count >= required_no_new:
                print(f"  Cache saturated after {i} probes. Final size: {after}")
                return True
        await asyncio.sleep(random.uniform(3, 5))
    print(f"  Max probes ({max_probes}) reached. Cache size: {len(quiz_cache)}")
    return False

# ========== STATS AND TODAY QUIZ CHECK ==========
def find_value(data, *keys):
    if isinstance(data, dict):
        for k in keys:
            if k in data:
                return data[k]
        for v in data.values():
            r = find_value(v, *keys)
            if r is not None:
                return r
    elif isinstance(data, list):
        for item in data:
            r = find_value(item, *keys)
            if r is not None:
                return r
    return None

async def fetch_stats(session, cookies):
    for ep in ["/user/me", "/me", "/user/profile", "/profile"]:
        try:
            async with session.get(f"{BASE_URL}{ep}", headers=get_headers(), cookies=cookies) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    elo = find_value(data, "elo", "eloScore", "elo_score", "rating")
                    played = find_value(data, "totalChallengesPlayed",
                                        "total_challenges_played", "challengesPlayed",
                                        "challenges_played", "gamesPlayed",
                                        "totalGames", "total_games", "played")
                    print(f"  [STATS] fetched: elo={elo}, played={played}")
                    return elo, played
        except Exception as e:
            print(f"  [STATS] error on {ep}: {e}")
    return None, None

async def check_today_quiz_played(session, cookies):
    try:
        async with session.get(f"{BASE_URL}/attempt/check", headers=get_headers(), cookies=cookies) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if data.get("hasPlayedToday") is not None:
                    return data.get("hasPlayedToday")
    except:
        pass
    return False

# ========== VALIDATE ANSWER WITH STATUS (improved backoff) ==========
async def validate_answer_with_status(session, cookies, attempt_id, question,
                                      selected_answer, time_spent, total_time_used=None,
                                      retries=5):
    payload = {
        "_id": attempt_id,
        "questionId": question["_id"],
        "question": question.get("question") or "",
        "selectedAnswer": selected_answer,
        "timeSpent": time_spent,
    }
    if total_time_used is not None:
        payload["totalTimeUsed"] = total_time_used
    for attempt_num in range(retries):
        try:
            async with session.post(
                f"{BASE_URL}/attempt/validate",
                headers=get_headers(),
                cookies=cookies,
                json=payload,
            ) as resp:
                status = resp.status
                if status == 429:
                    wait = min(30, (2 ** attempt_num) + random.uniform(0.5, 2.0))
                    print(f"  [API] Rate limited (429). Waiting {wait:.1f}s before retry {attempt_num+1}/{retries}")
                    await asyncio.sleep(wait)
                    continue
                data = await resp.json(content_type=None)
                if not data:
                    await asyncio.sleep(1)
                    continue
                if data.get("duplicateSubmission"):
                    print(f"  [API] duplicate submission")
                    return None, status
                for entry in data.get("data", {}).get("QuestionsAttempted", []):
                    if entry.get("questionId") == question["_id"]:
                        is_correct = entry.get("isCorrect", False)
                        print(f"  [API] answer submitted: correct={is_correct}, http_status={status}")
                        return is_correct, status
                return False, status
        except Exception as e:
            print(f"  [API] validate exception: {e}")
            await asyncio.sleep(2)
            continue
    return False, 500

# ========== SYNCHRONIZED PERFECT ATTEMPT (with final barrier) ==========
async def create_synchronized_perfect_attempt(session, quiz_cache, lo, hi, cookies, barrier, fixed_time_per_question, learn_from_failures=True):
    """
    Generate an attempt but only submit the last question after the barrier is cleared.
    Returns (anon_cookie, elapsed, correct, total, learned, last_status)
    """
    print(f"  [ATTEMPT] Creating perfect attempt...")
    attempt_id, questions, anon_cookie = await generate_attempt(session, cookies=cookies)
    if not attempt_id:
        return None, None, 0, 0, 0, None

    total = len(questions)
    total_time = 0.0
    correct = 0
    for i, q in enumerate(questions[:-1]):
        qid = q["_id"]
        answer = quiz_cache.get(qid)
        if not answer:
            answer = q["options"][0] if q.get("options") else ""
        t = fixed_time_per_question
        total_time += t
        val_cookies = {**cookies, "anon_attempt_id": anon_cookie} if anon_cookie else cookies
        result, _ = await validate_answer_with_status(
            session, val_cookies, attempt_id, q, answer, t, total_time_used=None
        )
        if result is True:
            correct += 1
        await asyncio.sleep(random.uniform(0.15, 0.3))

    print(f"  [ATTEMPT] Waiting at final barrier...")
    await barrier.wait()
    print(f"  [ATTEMPT] Submitting last question simultaneously!")

    last_q = questions[-1]
    last_answer = quiz_cache.get(last_q["_id"]) or (last_q["options"][0] if last_q.get("options") else "")
    t = fixed_time_per_question
    total_time += t
    val_cookies = {**cookies, "anon_attempt_id": anon_cookie} if anon_cookie else cookies
    result, last_status = await validate_answer_with_status(
        session, val_cookies, attempt_id, last_q, last_answer, t,
        total_time_used=round(total_time, 2)
    )
    if result is True:
        correct += 1

    newly_learned = 0
    if learn_from_failures:
        probe_stats = load_probe_stats()
        newly_learned = await update_cache_from_attempt(session, attempt_id, quiz_cache, probe_stats)

    return anon_cookie, round(total_time, 1), correct, total, newly_learned, last_status

# ========== OTHER REQUIRED FUNCTIONS ==========
async def create_perfect_attempt_with_learning(session, quiz_cache, lo, hi,
                                               learn_from_failures=True,
                                               use_ai=True,
                                               cookies=None,
                                               fixed_time_per_question=None):
    if cookies is None:
        cookies = {}
    print(f"  [ATTEMPT] Creating perfect attempt (standard)...")
    attempt_id, questions, anon_cookie = await generate_attempt(session, cookies=cookies)
    if not attempt_id:
        return None, None, 0, 0, 0, 0, None

    total_time = 0.0
    correct = 0
    fallback_count = 0
    total = len(questions)
    last_status = None

    for i, q in enumerate(questions):
        is_last = i == total - 1
        qid = q["_id"]
        answer = quiz_cache.get(qid)
        if answer is None and use_ai:
            answer = await _ai_answer(session, q.get("question", ""), q.get("options", []))
            if answer is None:
                answer = q["options"][0] if q.get("options") else ""
                fallback_count += 1
        elif answer is None:
            answer = q["options"][0] if q.get("options") else ""
            fallback_count += 1

        if fixed_time_per_question is not None:
            t = fixed_time_per_question
        else:
            t = round(random.uniform(lo, hi), 2)
        total_time += t
        val_cookies = {**cookies, "anon_attempt_id": anon_cookie} if anon_cookie else cookies
        result, status = await validate_answer_with_status(
            session, val_cookies, attempt_id, q, answer, t,
            total_time_used=round(total_time, 2) if is_last else None,
        )
        if result is True:
            correct += 1
        if is_last:
            last_status = status
        await asyncio.sleep(random.uniform(0.15, 0.3))

    newly_learned = 0
    if learn_from_failures:
        probe_stats = load_probe_stats()
        newly_learned = await update_cache_from_attempt(session, attempt_id, quiz_cache, probe_stats)

    print(f"  [ATTEMPT] Completed: {correct}/{total} correct, {newly_learned} learned, final HTTP {last_status}")
    return anon_cookie, round(total_time, 1), correct, total, fallback_count, newly_learned, last_status

# AI helpers
_AI_CONFIG = None
_ASK_AI = None

def _get_ai():
    global _AI_CONFIG, _ASK_AI
    if _ASK_AI is None:
        try:
            from ai_solver import AIConfig, ask_ai
            _AI_CONFIG = AIConfig.load()
            _ASK_AI = ask_ai
        except ImportError:
            _AI_CONFIG = None
            _ASK_AI = None
    return _AI_CONFIG, _ASK_AI

async def _ai_answer(session, question_text, options):
    cfg, ask_func = _get_ai()
    if cfg and cfg.is_configured and ask_func and question_text:
        try:
            answer = await ask_func(cfg, question_text, options, session=session)
            if answer and any(normalize_answer(answer) == normalize_answer(opt) for opt in options):
                return answer
            if answer and len(answer) == 1 and answer.upper() in "ABCD":
                idx = ord(answer.upper()) - ord('A')
                if 0 <= idx < len(options):
                    return options[idx]
        except Exception:
            pass
    return None

async def export_answers_file(session, quiz_cache, question_meta, out_path="correct_answers_today.json"):
    output = []
    for qid, correct_option in quiz_cache.items():
        meta = question_meta.get(qid, {})
        output.append({
            "question_id": qid,
            "question": meta.get("question", "(unknown)"),
            "correct_answer": correct_option,
        })
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    return out_path

async def discover_for_attempt(session, questions, quiz_cache, question_meta):
    unknown = [q for q in questions if q["_id"] not in quiz_cache]
    if not unknown:
        return quiz_cache
    tried = {}
    probe_stats = load_probe_stats()
    for i in range(1, min(12, len(unknown) * 2)):
        await run_probe_attempt(session, quiz_cache, question_meta, tried, i, probe_stats)
    return quiz_cache

async def verify_attempt(session, anon_id, retries=4):
    for _ in range(retries):
        try:
            async with session.get(
                f"{BASE_URL}/attempt/{anon_id}",
                headers=get_headers(),
                cookies={"anon_attempt_id": anon_id},
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(5)
                    continue
                data = await resp.json(content_type=None)
                if data and data.get("success"):
                    attempted = data.get("attemptedQuestions", [])
                    return {
                        "correct": sum(1 for a in attempted if a.get("isCorrect")),
                        "total": data.get("totalQuestions", 15),
                        "time": data.get("attemptData", {}).get("timeTakenTotal", 0),
                    }
        except:
            await asyncio.sleep(2)
    return None

async def cram_answers(session, quiz_cache, question_meta, max_attempts=200):
    tried_options = {}
    probe_stats = load_probe_stats()
    total_learned = 0
    for i in range(1, max_attempts + 1):
        learned = await run_probe_attempt(session, quiz_cache, question_meta, tried_options, i, probe_stats)
        total_learned += learned
        await asyncio.sleep(random.uniform(3, 5))
    return total_learned

async def play_quiz_with_ai(session, ai_cfg, quiz_cache, cookies=None, dry_run=False):
    return None

async def send_link_request(session, cookies, anon_id, req_id):
    c = dict(cookies)
    c["anon_attempt_id"] = anon_id
    async with session.get(f"{BASE_URL}/attempt/linkAnon", headers=get_headers(), cookies=c) as resp:
        return resp.status

async def main():
    print("Run bot.py instead.")

if __name__ == "__main__":
    asyncio.run(main())