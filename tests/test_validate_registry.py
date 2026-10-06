import importlib.util, json, tempfile, unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('validator',root/'scripts/validate-registry.py');v=importlib.util.module_from_spec(s);s.loader.exec_module(v)

class RegistryTests(unittest.TestCase):
    def validate_text(self,text):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'plugins.json';p.write_text(text);return v.validate(p)
    def test_published_registry_structure(self):self.assertGreater(v.validate(root/'plugins.json'),0)
    def test_accidental_object_in_tag_array_is_rejected(self):
        text='{"plugins":[{"id":"store","tags":["plugins" {"id":"realm-toggle"}]}]}'
        with self.assertRaisesRegex(ValueError,'line 1, column'):self.validate_text(text)
    def test_duplicate_ids_and_nested_tags_are_rejected(self):
        data=json.loads((root/'plugins.json').read_text())
        data['plugins'].append(dict(data['plugins'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.validate_text(json.dumps(data))
        data['plugins'].pop();data['plugins'][0]['tags'].append({'id':'misplaced-plugin'})
        with self.assertRaisesRegex(ValueError,'only strings'):self.validate_text(json.dumps(data))

if __name__=='__main__':unittest.main()
