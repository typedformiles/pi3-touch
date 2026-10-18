"""Off-Pi tests for the Trivia app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: Open Trivia DB is faked, with its response codes and its 5-second rule.
"""
import base64
import os
import random
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "trivia"))
import trivia_data as td  # noqa: E402


def b64(s):
    return base64.b64encode(s.encode()).decode()


def question(n, kind="multiple"):
    if kind == "boolean":
        return {"type": b64("boolean"), "difficulty": b64("easy"), "category": b64("General Knowledge"),
                "question": b64(f"Statement {n} is true."), "correct_answer": b64("True"),
                "incorrect_answers": [b64("False")]}
    return {"type": b64("multiple"), "difficulty": b64("hard"), "category": b64("Entertainment: Film"),
            "question": b64(f"Question {n}: who directed it?"), "correct_answer": b64(f"Right {n}"),
            "incorrect_answers": [b64(f"Wrong {n}{x}") for x in "abc"]}


class FakeOtdb:
    """Answers like opentdb.com: a token, then batches until the topic runs out."""

    def __init__(self, available=100, clock=None):
        self.calls, self.available, self.clock, self.fail = [], available, clock, None
        self.token_lost = False
        self.last = None

    def __call__(self, url, timeout=15):
        now = self.clock()
        self.calls.append(url[len(td.API):])
        if self.fail:
            raise self.fail
        if self.last is not None and now - self.last < 5:
            return {"response_code": 5, "results": []}
        self.last = now
        if "command=request" in url:
            return {"response_code": 0, "token": "tok"}
        if "command=reset" in url:
            self.available = 100
            return {"response_code": 0}
        if self.token_lost:
            return {"response_code": 3, "results": []}
        amount = int(url.split("amount=")[1].split("&")[0])
        if self.available == 0:
            return {"response_code": 4, "results": []}
        if amount > self.available:
            return {"response_code": 1, "results": []}
        self.available -= amount
        return {"response_code": 0, "results": [question(i, "boolean" if i % 4 == 0 else "multiple")
                                                for i in range(amount)]}


class TestQuestions(unittest.TestCase):
    def test_unpack(self):
        q = td.unpack(question(7), random.Random(1))
        self.assertEqual((q["q"], q["correct"], q["topic"], q["difficulty"]),
                         ("Question 7: who directed it?", "Right 7", "Film", "hard"))
        self.assertEqual(sorted(q["answers"]), ["Right 7", "Wrong 7a", "Wrong 7b", "Wrong 7c"])
        self.assertEqual(td.unpack(question(1, "boolean"))["answers"], ["True", "False"], "always True, False")

    def test_answers_are_shuffled(self):
        rng = random.Random(3)
        firsts = {td.unpack(question(1), rng)["answers"].index("Right 1") for _ in range(30)}
        self.assertGreater(len(firsts), 1, "the right answer isn't always in the same place")

    def test_names_and_verdicts(self):
        self.assertEqual(td.topic_name(31), "Anime")
        self.assertEqual(len({c for c, _ in td.TOPICS}), len(td.TOPICS))
        self.assertEqual(td.verdict(10, 10), "Perfect round!")
        self.assertEqual(td.verdict(0, 10), "Better luck next time")
        self.assertEqual(td.verdict(0, 0), "")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = 1000.0
        self.api = FakeOtdb(clock=lambda: self.t)
        self.store = td.Store(cache_dir=self.dir, fetch=self.api, clock=lambda: self.t, rng=random.Random(2))
        self.store.want = td.key(11, "hard")

    def step(self, n=1):
        for _ in range(n):
            self.store.refresh()
            self.t += td.GAP

    def test_token_then_questions_then_waits(self):
        self.step()
        self.assertEqual(self.store.token, "tok")
        self.step()
        self.assertEqual(self.store.count(self.store.want), td.BATCH)
        self.assertIn("&category=11&difficulty=hard", self.api.calls[-1])
        self.assertIn("token=tok", self.api.calls[-1])
        n = len(self.api.calls)
        self.step(3)
        self.assertEqual(len(self.api.calls), n, "stocked: no more calls")
        self.assertEqual(len(self.store.take(self.store.want, 10)), 10)
        self.step()
        self.assertEqual(self.store.count(self.store.want), 10 + td.BATCH, "topped up below the stock level")

    def test_keeps_the_five_second_gap(self):
        self.store.refresh()
        self.t += 1
        self.assertFalse(self.store.refresh(), "too soon")

    def test_anything_and_any_level_leave_the_filters_off(self):
        self.store.want = td.key(0, "any")
        self.step(2)
        self.assertNotIn("category", self.api.calls[-1])
        self.assertNotIn("difficulty", self.api.calls[-1])

    def test_a_topic_running_out(self):
        self.api.available = 23
        self.step(2)                                   # token, 20
        self.store.take(self.store.want, 20)
        self.step()                                    # 20 asked, 3 left: code 1
        self.step()                                    # asks for 5: still code 1
        self.assertIn(self.store.want, self.store.exhausted)
        self.assertEqual(self.store.error(), "seen them all")
        self.store.reset_topic(self.store.want)
        self.assertEqual(self.store.exhausted, set())

    def test_stock_survives_a_restart(self):
        self.step(2)
        self.store.remember(11, "hard")
        again = td.Store(cache_dir=self.dir, fetch=self.api, clock=lambda: self.t)
        self.assertEqual(again.count(td.key(11, "hard")), td.BATCH)
        self.assertEqual(again.choice, {"category": 11, "difficulty": "hard"})
        self.assertEqual(again.token, "tok")

    def test_offline_backs_off(self):
        self.api.fail = urllib.error.URLError("down")
        self.step()
        self.assertEqual(self.store.error(), "offline")
        self.assertFalse(self.store.refresh(), "waits a minute")
        self.t += td.RETRY_AFTER
        self.api.fail = None
        self.assertTrue(self.store.refresh())

    def test_lost_token_gets_a_new_one(self):
        self.step()
        self.api.token_lost = True                     # unused for 6 hours, the API forgets it
        self.step()
        self.assertIsNone(self.store.token)
        self.api.token_lost = False
        self.step(2)
        self.assertEqual((self.store.token, self.store.count(self.store.want)), ("tok", td.BATCH))


class TestConfig(unittest.TestCase):
    def test_trivia_json(self):
        cfg = td.load_config()
        self.assertIn(cfg["difficulty"], td.DIFFICULTIES)
        self.assertIn(cfg["category"], dict(td.TOPICS))
        self.assertTrue(1 <= cfg["round"] <= 50)


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import trivia_display
except ImportError:
    trivia_display = None


@unittest.skipUnless(trivia_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        import pygame
        self.t = 1000.0
        self.store = td.Store(cache_dir=tempfile.mkdtemp(), fetch=FakeOtdb(clock=lambda: self.t),
                              clock=lambda: self.t)
        self.app = trivia_display.TriviaDisplay(td.load_config(), self.store, screen=pygame.Surface((480, 800)))

    def fill(self):
        for _ in range(2):
            self.store.refresh()
            self.t += td.GAP

    def tap(self, name):
        self.app.draw()
        self.app.tap(self.app.buttons[name].center)

    def test_first_run_picks_a_topic(self):
        self.assertEqual(self.app.mode, "pick")
        self.app.draw()
        self.tap("cat:22")
        self.tap("level:easy")
        self.assertEqual(self.store.want, "22-easy")

    def test_a_whole_round(self):
        self.fill()
        self.store.want = td.key(0, "any")
        self.fill()
        self.tap("start")
        self.assertEqual((self.app.mode, len(self.app.questions)), ("question", 10))
        for k in range(10):
            q = self.app.question
            right = q["answers"].index(q["correct"])
            self.tap(f"answer{right if k % 2 == 0 else (right + 1) % len(q['answers'])}")
            self.tap("next")
        self.assertEqual((self.app.mode, sum(self.app.results)), ("done", 5))
        self.app.draw()
        self.tap("next")                                 # play again
        self.assertEqual(self.app.mode, "question")

    def test_one_answer_per_question(self):
        self.fill()
        self.app.start_round()
        self.tap("answer0")
        self.app.draw()
        self.assertFalse(any(k.startswith("answer") for k in self.app.buttons))
        self.assertEqual(len(self.app.results), 1)

    def test_waits_for_questions_then_starts(self):
        self.app.mode, self.app.questions = "question", []
        self.app.draw()
        self.assertIsNone(self.app.question)
        self.fill()
        self.app.frame()
        self.assertEqual(len(self.app.questions), 10)

    def test_redraws_only_when_something_changes(self):
        self.assertTrue(self.app.frame())
        self.assertFalse(self.app.frame())
        self.fill()
        self.assertTrue(self.app.frame())
        self.app.last_touch -= trivia_display.DIM_AFTER + 1
        self.assertTrue(self.app.frame())
        self.assertFalse(self.app.frame())


if __name__ == "__main__":
    unittest.main()
