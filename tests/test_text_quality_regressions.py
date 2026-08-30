import unittest

from pipeline.groq_text_cleaner import validate_cleaned_text


class TextQualityRegressionTests(unittest.TestCase):
    def test_minimal_ocr_corrections_are_accepted(self) -> None:
        source = (
            "Statü anlayışı l 776'dan bu yana maddi başarıyla "
            "bir tutulur olmuştur. Bu roma nındaki kişi restorana "
            "gir di. Akşam yeme ği için sözleşmiştir."
        )
        corrected = (
            "Statü anlayışı 1776'dan bu yana maddi başarıyla "
            "bir tutulur olmuştur. Bu romanındaki kişi restorana "
            "girdi. Akşam yemeği için sözleşmiştir."
        )

        validation = validate_cleaned_text(source, corrected)

        self.assertTrue(validation["valid"])
        self.assertIn("bu yana", corrected)
        self.assertIn("bir tutulur olmuştur", corrected)

    def test_interpretive_rewrite_is_rejected(self) -> None:
        source = (
            "Statü anlayışı l 776'dan bu yana maddi başarıyla "
            "bir tutulur olmuştur."
        )
        rewritten = (
            "Statü anlayışı 1776'dan beri maddi başarıyla "
            "örtüşmüştür."
        )

        validation = validate_cleaned_text(source, rewritten)

        self.assertFalse(validation["valid"])
        self.assertLess(validation["similarity_ratio"], 0.90)


if __name__ == "__main__":
    unittest.main()