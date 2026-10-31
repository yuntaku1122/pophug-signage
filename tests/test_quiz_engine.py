import os, random, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quiz_engine import *


def mkq(i, ans=0, **kw):
    q = {"id": f"q{i}", "question": {"text": f"問題{i}"},
         "choices": [{"text": "あ"}, {"image": "b.png"}], "answer": ans}
    q.update(kw)
    return q


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = QuizStore(self.d)

    def sess(self, n=3, **settings):
        settings.setdefault("quiz_round_size", 0)   # 既存テストはラウンド無しで検証（ラウンドは専用テスト）
        self.store.save_questions([mkq(i) for i in range(1, n + 1)])
        return QuizSession(self.store, settings, rng=random.Random(1), now=0)

    def test_validation(self):
        self.assertIsNotNone(validate_question({"question": {}, "choices": [{}, {}], "answer": 0})[1])
        self.assertIsNotNone(validate_question(mkq(1, choices=[{"text": "a"}]))[1])
        self.assertIsNotNone(validate_question(mkq(1, choices=[{"text": "a"}, {}]))[1])
        self.assertIsNotNone(validate_question(mkq(1, ans=2))[1])
        self.assertIsNone(validate_question(mkq(1))[1])            # テキスト+画像の混在OK
        q, _ = validate_question({"question": {"image": "x.png"}, "choices": [{"image": "a"}, {"image": "b"}], "answer": 1})
        self.assertEqual(q["explanation"], {"text": "", "image": ""})  # 画像のみ・解説省略OK

    def test_duplicate_ids_and_atomic_roundtrip(self):
        with self.assertRaises(ValueError):
            self.store.save_questions([mkq(1), mkq(1)])
        self.store.save_questions([mkq(1), mkq(2)])
        self.assertEqual([q["id"] for q in self.store.load_questions()], ["q1", "q2"])

    def test_corrupt_file_is_empty(self):
        open(os.path.join(self.d, QUESTIONS_FILENAME), "w").write("{broken")
        self.assertEqual(self.store.load_questions(), [])
        self.assertFalse(self.store.has_questions())

    def test_sequential_wraps(self):
        s = self.sess(3)
        seen = [s.current["id"]]
        for t in range(1, 6):
            s.press(0, t * 100); s.press(0, t * 100 + 10)   # 回答→次へ
            seen.append(s.current["id"])
        self.assertEqual(seen, ["q1", "q2", "q3", "q1", "q2", "q3"])

    def test_random_covers_all_no_immediate_repeat(self):
        s = self.sess(4, quiz_order=ORDER_RANDOM)
        seq = [s.current["id"]]
        for t in range(1, 40):
            s.press(0, t * 100); s.press(0, t * 100 + 10)
            seq.append(s.current["id"])
        for i in range(0, 40, 4):
            self.assertEqual(sorted(seq[i:i + 4]), ["q1", "q2", "q3", "q4"])  # 一巡で全問
        self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))

    def test_button_advance_without_wait(self):
        s = self.sess(2)
        self.assertTrue(s.press(0, 10))                 # 回答
        self.assertEqual(s.state, STATE_RESULT)
        self.assertFalse(s.press(1, 10.1))              # チャタリング防止の0.3秒だけ無視
        self.assertEqual(s.state, STATE_RESULT)
        self.assertEqual(self.store.get_counts("q1"), (1, 0))  # 多重集計されない
        self.assertTrue(s.press(1, 10.4))               # 0.4秒後には次へ進める
        self.assertEqual((s.state, s.current["id"]), (STATE_QUESTION, "q2"))

    def test_result_idle_30s_default_and_custom(self):
        s = self.sess(2)
        s.press(0, 0)
        self.assertFalse(s.tick(29.9))
        self.assertTrue(s.tick(30.0))
        self.assertEqual(s.current["id"], "q2")
        s2 = self.sess(2, quiz_result_idle_seconds=10)
        s2.press(0, 0)
        self.assertTrue(s2.tick(10))

    def test_question_idle_skips_without_counting(self):
        s = self.sess(2, quiz_question_idle_seconds=20)
        self.assertFalse(s.tick(19))
        self.assertTrue(s.tick(20))
        self.assertEqual(s.current["id"], "q2")
        self.assertEqual(self.store.get_counts("q1"), (0, 0))
        s0 = self.sess(2, quiz_question_idle_seconds=0)
        self.assertFalse(s0.tick(99999))                # 0=無効

    def test_skip_shows_no_answer_and_changes_question(self):
        s = self.sess(3, quiz_question_idle_seconds=20)
        self.assertTrue(s.tick(20))
        self.assertEqual((s.state, s.selected, s.current["id"]), (STATE_QUESTION, None, "q2"))
        self.assertEqual(s.last_transition, "skipped")       # 結果・解説画面には行かない
        self.assertFalse(s.is_correct())
        s.press(0, 21); self.assertEqual(s.state, STATE_RESULT)
        s.tick(21 + 30); self.assertEqual(s.last_transition, "result_idle")

    def test_skipped_question_returns_sequential(self):
        s = self.sess(3, quiz_question_idle_seconds=20)
        seen = [s.current["id"]]
        for t in range(1, 5):
            s.tick(t * 100); seen.append(s.current["id"])    # 全部スキップし続ける
        self.assertEqual(seen, ["q1", "q2", "q3", "q1", "q2"])
        self.assertEqual(self.store.get_counts("q1"), (0, 0))

    def test_skipped_question_returns_random_within_round(self):
        for seed in range(30):
            self.store.save_questions([mkq(i) for i in range(1, 5)])
            s = QuizSession(self.store, {"quiz_order": ORDER_RANDOM, "quiz_question_idle_seconds": 20},
                            rng=random.Random(seed), now=0)
            first = s.current["id"]
            s.tick(100)
            self.assertNotEqual(s.current["id"], first)       # 問題は必ず入れ替わる
            seq = []
            for t in range(2, 6):
                seq.append(s.current["id"]); s.press(0, t * 100); s.press(0, t * 100 + 10)
            self.assertIn(first, seq)                         # スキップした問題がまた出題される

    def test_correctness_and_percentages(self):
        s = self.sess(1)
        for t, c in enumerate([0, 0, 1]):
            s.press(c, t * 100); s.press(0, t * 100 + 5)
        self.assertEqual(self.store.get_counts("q1"), (2, 1))
        self.assertEqual(percentages(2, 1), (67, 33))
        self.assertEqual(percentages(0, 0), (0, 0))
        s.press(0, 1000)
        self.assertTrue(s.is_correct())
        self.assertEqual(s.result_percentages(), (75, 25))

    def test_stats_buffer_flush_and_reset(self):
        self.store.record_answer("q1", 0)
        self.assertEqual(QuizStore(self.d).get_counts("q1"), (0, 0))  # 未flushはディスクに無い
        self.store.flush()
        self.assertEqual(QuizStore(self.d).get_counts("q1"), (1, 0))
        for _ in range(STATS_FLUSH_EVERY):
            self.store.record_answer("q2", 1)                          # 件数到達で自動flush
        self.assertEqual(QuizStore(self.d).get_counts("q2"), (0, STATS_FLUSH_EVERY))
        self.store.reset_stats("q1")
        self.assertEqual(QuizStore(self.d).get_counts("q1"), (0, 0))
        self.store.reset_stats()
        self.assertEqual(QuizStore(self.d).get_counts("q2"), (0, 0))

    def test_empty_store(self):
        s = QuizSession(self.store, now=0)
        self.assertTrue(s.is_empty)
        self.assertFalse(s.press(0, 1)); self.assertFalse(s.tick(999))

    def test_settings_validation(self):
        ok, err = validate_quiz_settings({"quiz_order": "random", "quiz_result_idle_seconds": "45"})
        self.assertEqual((ok, err), ({"quiz_order": "random", "quiz_result_idle_seconds": 45}, []))
        _, err = validate_quiz_settings({"quiz_order": "x", "quiz_result_idle_seconds": 1, "foo": 1})
        self.assertEqual(len(err), 3)


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.store = QuizStore(tempfile.mkdtemp())
        self.store.save_questions([mkq(i, ans=0) for i in range(1, 7)])  # 正解は全問A

    def mk(self, **st):
        base = {"quiz_round_size": 3, "quiz_question_idle_seconds": 60}
        base.update(st)
        return QuizSession(self.store, base, rng=random.Random(1), now=0)

    def answer(self, s, choice, t):
        s.press(choice, t); s.press(0, t + 10)  # 回答→先へ
        return t + 100

    def test_round_score_and_end_screen(self):
        s = self.mk()
        self.assertEqual(s.round_progress(), (1, 3, 0))
        t = self.answer(s, 0, 0)                      # 正解
        self.assertEqual(s.round_progress(), (2, 3, 1))
        t = self.answer(s, 1, t)                      # 不正解
        self.assertEqual(s.round_progress(), (3, 3, 1))
        s.press(0, t); self.assertEqual(s.state, STATE_RESULT)
        s.press(0, t + 10)                            # 最後の結果→成績発表
        self.assertEqual((s.state, s.last_transition), (STATE_ROUND_END, "round_end"))
        self.assertEqual((s.round_answered, s.round_correct), (3, 2))   # 2/3問正解
        self.assertFalse(s.press(0, t + 10.1))        # 発表直後はチャタリング防止のみ
        self.assertTrue(s.press(0, t + 10.5))
        self.assertEqual((s.state, s.round_answered, s.round_correct), (STATE_QUESTION, 0, 0))
        self.assertEqual(s.current["id"], "q4")       # 出題順は続きから（次の人用）

    def test_round_end_idle_starts_next_round(self):
        s = self.mk(quiz_round_end_seconds=20)
        t = 0
        for _ in range(2): t = self.answer(s, 0, t)
        s.press(0, t); s.press(0, t + 10)
        self.assertEqual(s.state, STATE_ROUND_END)
        self.assertFalse(s.tick(t + 10 + 19)); self.assertTrue(s.tick(t + 10 + 20))
        self.assertEqual((s.state, s.last_transition), (STATE_QUESTION, "round_restart"))

    def test_abandon_resets_round_but_keeps_global_stats(self):
        s = self.mk(quiz_question_idle_seconds=20)
        t = self.answer(s, 0, 0); t = self.answer(s, 1, t)       # 2問答えた
        self.assertEqual(s.round_answered, 2)
        self.assertTrue(s.tick(t + 20))                          # 次の問題で無回答→やめたと見なす
        self.assertEqual((s.round_answered, s.round_correct, s.last_transition), (0, 0, "abandoned"))
        self.assertEqual(s.current["id"], "q1")                  # 登録順なら最初から
        self.assertEqual(self.store.get_counts("q1"), (1, 0))    # 全員分の集計は残る
        self.assertEqual(self.store.get_counts("q2"), (0, 1))

    def test_no_progress_skip_is_not_abandon(self):
        s = self.mk(quiz_question_idle_seconds=20)
        s.tick(20)
        self.assertEqual((s.last_transition, s.current["id"]), ("skipped", "q2"))

    def test_hold_reset_from_any_state(self):
        for steps in ("question", "result", "round_end"):
            s = self.mk(); t = self.answer(s, 0, 0)
            if steps == "result": s.press(0, 5000)
            if steps == "round_end":
                t = self.answer(s, 0, t); s.press(0, t); s.press(0, t + 10)
                self.assertEqual(s.state, STATE_ROUND_END)
            self.assertTrue(s.hold_reset(9999))
            self.assertEqual((s.state, s.round_answered, s.round_correct, s.current["id"], s.last_transition),
                             (STATE_QUESTION, 0, 0, "q1", "reset"))
        self.assertEqual(self.store.get_counts("q1")[0] >= 1, True)

    def test_shift_time_pauses_idle(self):
        s = self.mk(quiz_question_idle_seconds=20)
        s.shift_time(100)                       # 100秒隠れていた分を差し引く（last_event_time=0→100）
        self.assertFalse(s.tick(110))           # 見かけ上10秒しか経っていない
        self.assertTrue(s.tick(120))

    def test_round_size_capped_by_question_count_and_zero_disables(self):
        self.assertEqual(self.mk(quiz_round_size=50).round_size, 6)
        s = self.mk(quiz_round_size=0)
        self.assertEqual(s.round_progress(), (0, 0, 0))
        t = 0
        for _ in range(10): t = self.answer(s, 0, t)
        self.assertEqual((s.state, s.round_answered), (STATE_QUESTION, 0))  # 成績発表に入らない

    def test_button_color_settings(self):
        for c in ("blue", "yellow", "green", "white", "red"):
            ok, err = validate_quiz_settings({"quiz_color_a": c, "quiz_color_b": c})
            self.assertEqual((err, ok), ([], {"quiz_color_a": c, "quiz_color_b": c}))
        _, err = validate_quiz_settings({"quiz_color_a": "pink", "quiz_color_b": ""})
        self.assertEqual(len(err), 2)

    def test_validation_of_new_settings(self):
        ok, err = validate_quiz_settings({"quiz_round_size": 0, "quiz_round_end_seconds": 20, "quiz_hold_reset_seconds": 3})
        self.assertEqual(err, [])
        _, err = validate_quiz_settings({"quiz_round_size": 51, "quiz_round_end_seconds": 1, "quiz_hold_reset_seconds": 0})
        self.assertEqual(len(err), 3)


class ImportTests(unittest.TestCase):
    def test_parse_answer(self):
        for v, want in (("A", 0), ("ａ", 0), ("1", 0), ("b", 1), ("Ｂ", 1), ("2", 1), ("C", None), ("", None), (None, None)):
            self.assertEqual(parse_answer(v), want, v)

    def test_csv_header_and_positional(self):
        rows = parse_import_csv("問題,A,B,正解,解説\nQ1,あ,い,B,解\n".encode("utf-8-sig"))
        self.assertEqual(rows[0]["answer"], 1)
        self.assertEqual(rows[0]["explanation"]["text"], "解")
        rows = parse_import_csv("Q1,あ,い,A\n\nQ2,う,え,2\n".encode("cp932"))   # 見出し無し・空行・Shift_JIS
        self.assertEqual([r["answer"] for r in rows], [0, 1])
        self.assertEqual(rows[1]["question"]["text"], "Q2")

    def test_json_shapes(self):
        self.assertEqual(parse_import_json(b'[{"a":1}]'), [{"a": 1}])
        self.assertEqual(parse_import_json(b'{"questions":[{"a":1}]}'), [{"a": 1}])
        with self.assertRaises(ValueError):
            parse_import_json(b'{"x":1}')
        with self.assertRaises(ValueError):
            parse_import_json(b"{oops")

    def test_prepare_import(self):
        ex = [mkq(1)]
        rows = [mkq(1, answer=1), mkq(2), mkq(2)]            # q1とID衝突・q2は取り込み内で重複
        rows[0]["question"] = {"text": "別問題"}
        r = prepare_import(rows, ex, image_exists=lambda n: True)
        self.assertEqual(r["errors"], [])
        self.assertEqual(len(r["ok"]), 2)
        self.assertEqual(r["duplicates"], 1)
        self.assertEqual(len({q["id"] for q in ex + r["ok"]}), 3)      # IDは衝突しない
        # 登録済みと同じ内容は重複
        dup = dict(mkq(1), id="z")
        self.assertEqual(prepare_import([mkq(1)], [dup], image_exists=lambda n: True)["duplicates"], 1)

    def test_prepare_import_errors(self):
        r = prepare_import([{"question": {"text": "x"}, "choices": [{"text": "a"}, {"text": "b"}], "answer": None}], [])
        self.assertEqual(r["errors"][0][0], 1)
        long = {"question": {"text": "あ" * 201}, "choices": [{"text": "a"}, {"text": "b"}], "answer": 0}
        self.assertIn("長すぎ", prepare_import([long], [])["errors"][0][1])
        miss = mkq(1)
        self.assertIn("画像が見つかりません", prepare_import([miss], [], image_exists=lambda n: False)["errors"][0][1])
        trav = {"question": {"text": "x", "image": "../a.png"}, "choices": [{"text": "a"}, {"text": "b"}], "answer": 0}
        self.assertEqual(prepare_import([trav], [], image_exists=lambda n: True)["errors"][0][0], 1)
        many = [{"question": {"text": f"Q{i}"}, "choices": [{"text": "a"}, {"text": "b"}], "answer": 0}
                for i in range(MAX_QUESTIONS + 1)]
        self.assertIn("上限", prepare_import(many, [])["errors"][-1][1])

    def test_replace_ignores_existing(self):
        r = prepare_import([mkq(1)], [dict(mkq(1), id="z")], replace=True, image_exists=lambda n: True)
        self.assertEqual((len(r["ok"]), r["duplicates"], r["total_after"]), (1, 0, 1))


if __name__ == "__main__":
    unittest.main(verbosity=1)
