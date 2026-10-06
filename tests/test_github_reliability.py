import base64, importlib.util, io, json, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
S=Path(__file__).resolve().parents[1]/'scripts/community-registry-telegram-bot.py'
spec=importlib.util.spec_from_file_location('reliable_bot',S); b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class NetworkTests(unittest.TestCase):
    def test_safe_read_retries_connection_reset(self):
        with patch.object(b.urllib.request,'urlopen',side_effect=[ConnectionResetError('reset'),io.BytesIO(b'{"login":"owner"}')]) as request, patch.object(b.time,'sleep'):
            self.assertEqual(b.github('/user')['login'],'owner');self.assertEqual(request.call_count,2)
    def test_mutation_is_not_replayed_on_ambiguous_failure(self):
        with patch.object(b.urllib.request,'urlopen',side_effect=TimeoutError('timeout')) as request,patch.object(b.time,'sleep'):
            with self.assertRaises(b.TransportError):b.github('/reviews','POST',{'event':'APPROVE'})
            self.assertEqual(request.call_count,1)
    def test_unauthorized_is_not_retried(self):
        error=urllib.error.HTTPError('https://api.github.com/user',401,'bad',{},io.BytesIO(b'{"message":"Bad credentials"}'))
        with patch.object(b.urllib.request,'urlopen',side_effect=error) as request,patch.object(b.time,'sleep'):
            with self.assertRaises(b.ApiError) as caught:b.github('/user')
            self.assertEqual(caught.exception.code,401);self.assertEqual(request.call_count,1)
    def test_transient_gateway_retries(self):
        error=urllib.error.HTTPError('https://api.github.com/user',502,'bad',{},io.BytesIO(b'bad gateway'))
        with patch.object(b.urllib.request,'urlopen',side_effect=[error,io.BytesIO(b'{}')]) as request,patch.object(b.time,'sleep'):
            self.assertEqual(b.github('/user'),{});self.assertEqual(request.call_count,2)
    def test_transport_errors_redact_credentials(self):
        with patch.object(b,'TG_TOKEN','SECRET'),patch.object(b.urllib.request,'urlopen',side_effect=OSError('url contains SECRET')),patch.object(b.time,'sleep'):
            with self.assertRaises(b.TransportError) as error:b.http_json('https://api.telegram.org/botSECRET/getMe','POST')
            self.assertNotIn('SECRET',str(error.exception))
    def test_startup_network_loss_is_handled_in_loop(self):
        with patch.object(b,'TG_TOKEN','test'),patch.object(b,'GH_TOKEN','test'),patch.object(b,'load_state',return_value={}),patch.object(b,'telegram',side_effect=b.TransportError('offline')),patch.object(b.time,'sleep',side_effect=KeyboardInterrupt),patch('builtins.print') as log:
            with self.assertRaises(KeyboardInterrupt):b.main()
            self.assertTrue(log.called)

class MetadataTests(unittest.TestCase):
    def test_broken_json_names_file_revision_and_line(self):
        content=base64.b64encode(b'{"plugins": [\n {"id":"x"} {"id":"y"}\n]}').decode()
        with patch.object(b,'github',return_value={'content':content}):
            with self.assertRaises(b.RegistryValidationError) as error:b.registry_snapshot('owner/repo','abcdef123456789')
            text=b.moderation_error_message(error.exception)
            for value in ('plugins.json','owner/repo','abcdef123456','строка 2'):self.assertIn(value,text)
            self.assertNotIn('GitHub не выполнил',text)
    def test_invalid_metadata_never_calls_merge_api(self):
        pr={'state':'open','base':{'ref':'main'}}
        with patch.object(b,'is_moderator',return_value=True),patch.object(b,'github',return_value=pr) as github,patch.object(b,'changed_plugins',return_value=([], 'Некорректный plugins.json: строка 17')),patch.object(b,'send') as send:
            b.moderate({'chat':{'id':1}},'merge','17')
            self.assertTrue(all(len(call.args)==1 for call in github.call_args_list))
            self.assertIn('plugins.json',send.call_args.args[1]);self.assertNotIn('GitHub не выполнил',send.call_args.args[1])
    def test_code_only_pr_can_merge_without_package(self):
        pr={'state':'open','base':{'ref':'main'},'head':{'sha':'abc'}}
        with patch.object(b,'is_moderator',return_value=True),patch.object(b,'github',side_effect=[pr,[{'filename':'scripts/bot.py'}],{'merged':True}]) as github,patch.object(b,'changed_plugins',return_value=([],None)),patch.object(b,'send'),patch.object(b,'clear_pr_actions'),patch.object(b,'validate_archive') as validate,patch.object(b,'approve_registry_entries') as approve:
            b.moderate({'chat':{'id':1}},'merge','18')
            self.assertEqual(github.call_args.args[1],'PUT');validate.assert_not_called();approve.assert_not_called()
    def test_metadata_errors_are_not_cached_forever(self):
        pr={'number':1,'head':{'sha':'h'},'base':{'sha':'b'}};state={}
        with patch.object(b,'pr_items',return_value=[pr]),patch.object(b,'changed_plugins',side_effect=[([], 'temporary error'),([{'id':'x'}],None)]) as changed:
            b.fetch_prs(state);rows=b.fetch_prs(state)
            self.assertEqual(changed.call_count,2);self.assertIsNone(rows[0]['_registry_error'])
    def test_github_status_shows_account_and_access_without_token(self):
        with patch.object(b,'GH_TOKEN','PRIVATE'),patch.object(b,'github_login',return_value='owner'),patch.object(b,'github',return_value={'permissions':{'push':True}}):
            text=b.github_status();self.assertIn('owner',text);self.assertIn('есть',text);self.assertNotIn('PRIVATE',text)

if __name__=='__main__': unittest.main()
