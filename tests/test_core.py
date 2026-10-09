import threading
import unittest

from ema_tts import textprep
from ema_tts.speech_queue import PLAYING, SpeechQueue


class CleanTest(unittest.TestCase):
    def test_strips_markdown_and_skips_code(self):
        text = (
            "## Özet\n"
            "- **Kalın** madde\n"
            "- `config.json` dosyasına bak\n"
            "```python\nprint('x')\n```\n"
            "Detay için [belgeye](https://example.com) ve https://huggingface.co bak"
        )
        self.assertEqual(
            textprep.clean(text),
            "Özet. Kalın madde. config.json dosyasına bak. Kod bloğu. "
            "Detay için belgeye ve bağlantı bak.",
        )

    def test_table_rows_become_lists(self):
        text = "| Ad | Değer |\n|---|---|\n| hız | 1.0 |"
        self.assertEqual(textprep.clean(text), "Ad, Değer. hız, 1.0.")

    def test_keeps_snake_case(self):
        self.assertEqual(textprep.clean("use_lightning ayarı"), "use_lightning ayarı.")


class SplitTest(unittest.TestCase):
    def test_packs_sentences_under_limit(self):
        text = "Bir. İki cümle. Üç cümle burada."
        self.assertEqual(textprep.split(text, max_chars=16), ["Bir. İki cümle.", "Üç cümle burada."])

    def test_breaks_overlong_sentence(self):
        sentence = " ".join(["kelime"] * 40) + "."
        segments = textprep.split(sentence, max_chars=50)
        self.assertTrue(all(len(s) <= 50 for s in segments))
        self.assertEqual(" ".join(segments), sentence)


class QueueTest(unittest.TestCase):
    def test_add_rejects_empty_and_duplicates(self):
        q = SpeechQueue()
        self.assertIsNotNone(q.add("merhaba"))
        self.assertIsNone(q.add("  merhaba "))
        self.assertIsNone(q.add("   "))
        self.assertEqual(len(q.snapshot()), 1)

    def test_take_marks_playing_and_remove_cancels(self):
        q = SpeechQueue()
        first = q.add("bir")
        q.add("iki")
        taken = q.take(timeout=0)
        self.assertEqual(taken.id, first.id)
        self.assertEqual(q.snapshot()[0].state, PLAYING)
        q.remove(first.id)
        self.assertFalse(q.contains(first.id))
        self.assertEqual([i.text for i in q.snapshot()], ["iki"])

    def test_take_waits_for_add(self):
        q = SpeechQueue()
        threading.Timer(0.05, q.add, args=("geç",)).start()
        self.assertEqual(q.take(timeout=2).text, "geç")
        self.assertIsNone(SpeechQueue().take(timeout=0.01))


if __name__ == "__main__":
    unittest.main()
