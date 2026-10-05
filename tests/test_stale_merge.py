import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(os.environ.get('BOT_TEST_SCRIPT', Path(__file__).resolve().parents[1] / 'scripts/community-registry-telegram-bot.py'))
spec = importlib.util.spec_from_file_location('telegram_bot_stale_tests', SCRIPT)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

OPEN = {'number': 14, 'state': 'open', 'merged': False, 'base': {'ref': 'main'}, 'head': {'sha': 'reviewed'}, 'mergeable': True}
MERGED = {**OPEN, 'state': 'closed', 'merged': True}
MESSAGE = {'chat': {'id': 5, 'type': 'private'}, 'from': {'username': 'moderator'}, '_message_id': 100}


class StaleMergeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bot, 'is_moderator', return_value=True))
        self.send = self.enterContext(patch.object(bot, 'send'))
        self.telegram = self.enterContext(patch.object(bot, 'telegram', return_value={'ok': True}))
        if hasattr(bot, 'changed_plugins'):
            self.enterContext(patch.object(bot, 'changed_plugins', return_value=([{'id': 'example'}], None)))
            self.enterContext(patch.object(bot, 'validate_archive'))
            self.enterContext(patch.object(bot, 'approve_registry_entries'))

    def test_already_merged_does_not_try_to_merge_again(self):
        with patch.object(bot, 'github', return_value=MERGED) as github:
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertEqual(github.call_count, 1)
        self.assertIn('уже слит', self.send.call_args.args[1])
        self.assertEqual(self.telegram.call_args.args[0], 'editMessageReplyMarkup')
        buttons = self.telegram.call_args.args[1]['reply_markup']['inline_keyboard']
        self.assertFalse(any('callback_data' in button for row in buttons for button in row))

    def test_closed_unmerged_is_reported_separately(self):
        with patch.object(bot, 'github', return_value={**OPEN, 'state': 'closed'}) as github:
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertEqual(github.call_count, 1)
        self.assertIn('закрыт без слияния', self.send.call_args.args[1])

    def test_wrong_base_is_reported_and_not_merged(self):
        with patch.object(bot, 'github', return_value={**OPEN, 'base': {'ref': 'release'}}) as github:
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertEqual(github.call_count, 1)
        self.assertIn('ветку release', self.send.call_args.args[1])

    def test_successful_merge_clears_original_and_confirmation(self):
        def github(path, method='GET', payload=None):
            return {'merged': True} if method == 'PUT' else OPEN
        with patch.object(bot, 'github', side_effect=github):
            bot.moderate({**MESSAGE, '_source_message_id': 90}, 'merge', '14')
        edits = [call.args[1]['message_id'] for call in self.telegram.call_args_list if call.args[0] == 'editMessageReplyMarkup']
        self.assertEqual(set(edits), {90, 100})
        self.assertIn('смёржен', self.send.call_args.args[1])

    def test_concurrent_merge_api_failure_is_recognized(self):
        with patch.object(bot, 'github', side_effect=[OPEN, RuntimeError('already merged'), MERGED]) as github:
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertEqual(github.call_count, 3)
        self.assertIn('уже слит', self.send.call_args.args[1])

    def test_concurrent_merge_false_result_is_recognized(self):
        with patch.object(bot, 'github', side_effect=[OPEN, {'merged': False}, MERGED]):
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertIn('уже слит', self.send.call_args.args[1])

    def test_failed_keyboard_edit_does_not_hide_merge_success(self):
        self.telegram.side_effect = RuntimeError('message deleted')
        with patch.object(bot, 'github', side_effect=[OPEN, {'merged': True}]):
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertIn('смёржен', self.send.call_args.args[1])

    def test_stale_card_does_not_offer_confirmation(self):
        update = {'callback_query': {'id': 'callback', 'data': 'pr:merge:14',
                  'from': MESSAGE['from'], 'message': {'chat': MESSAGE['chat'], 'message_id': 100}}}
        with patch.object(bot, 'github', return_value=MERGED) as github:
            bot.handle_callback(update, {})
        self.assertEqual(github.call_count, 1)
        self.assertIn('уже слит', self.send.call_args.args[1])
        self.assertFalse(any(call.args[0] == 'sendMessage' for call in self.telegram.call_args_list))

    def test_confirmation_keeps_original_message_reference(self):
        update = {'callback_query': {'id': 'callback', 'data': 'pr:merge:14',
                  'from': MESSAGE['from'], 'message': {'chat': MESSAGE['chat'], 'message_id': 100}}}
        state = {}
        with patch.object(bot, 'github', return_value=OPEN):
            bot.handle_callback(update, state)
        self.assertEqual(state['action_messages']['5:14:merge'], 100)
        update['callback_query']['data'] = 'confirm:merge:14'
        update['callback_query']['message']['message_id'] = 101
        with patch.object(bot, 'moderate') as moderate:
            bot.handle_callback(update, state)
        self.assertEqual(moderate.call_args.args[0]['_source_message_id'], 100)
        self.assertEqual(moderate.call_args.args[0]['_message_id'], 101)
        self.assertEqual(state['action_messages'], {})

    def test_merge_error_is_not_misreported_as_success(self):
        with patch.object(bot, 'github', side_effect=[OPEN, RuntimeError('permission denied'), OPEN]):
            bot.moderate(MESSAGE, 'merge', '14')
        self.assertIn('permission denied', self.send.call_args.args[1])
        self.assertNotIn('уже слит', self.send.call_args.args[1])

    def test_terminal_keyboard_has_no_moderation_buttons(self):
        for pr in [MERGED, {**OPEN, 'state': 'closed'}]:
            keyboard = bot.pr_keyboard(pr)
            self.assertEqual(len(keyboard['inline_keyboard']), 1)
            self.assertFalse(any('callback_data' in button for row in keyboard['inline_keyboard'] for button in row))


if __name__ == '__main__':
    unittest.main()
