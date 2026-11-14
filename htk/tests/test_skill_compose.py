import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_compose", ROOT / "libexec/skill_compose.py")
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)


class CompositionTests(unittest.TestCase):
    def test_metadata_links_and_code_examples_survive_assembly(self):
        with tempfile.TemporaryDirectory(prefix="skill sources ") as directory:
            root = Path(directory)
            core, profile = root / "core/SKILL.md", root / "profile/SKILL.md"
            core.parent.mkdir()
            profile.parent.mkdir()
            (core.parent / "guide.md").write_text("Common reference")
            (profile.parent / "settings.md").write_text("Profile reference")
            core.write_text('---\nname: shared\ndescription: shared\n---\n'
                            'Shared instructions. [Guide](guide.md#details)\n'
                            '```md\n[Example](relative.md)\n```\n'
                            '[Web](https://example.test/a) [Local](#section)\n')
            profile.write_text('---\nname: personal\ndescription: custom\nmetadata:\n  tag: kept\n---\n'
                               'Profile additions. [Settings](settings.md)\n')
            result = compose.compose(core, profile)
            self.assertTrue(result.startswith('---\nname: personal\ndescription: custom\nmetadata:\n  tag: kept\n---'))
            self.assertIn('Shared instructions.', result)
            self.assertIn('Profile additions.', result)
            self.assertIn(f'[Guide](<{core.parent.resolve() / "guide.md"}#details>)', result)
            self.assertIn(f'[Settings](<{profile.parent.resolve() / "settings.md"}>)', result)
            self.assertIn('```md\n[Example](relative.md)\n```', result)
            self.assertIn('[Web](https://example.test/a) [Local](#section)', result)

    def test_incomplete_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'SKILL.md'
            path.write_text('No frontmatter')
            with self.assertRaisesRegex(ValueError, 'frontmatter'):
                compose.split_document(path)


if __name__ == '__main__':
    unittest.main()
