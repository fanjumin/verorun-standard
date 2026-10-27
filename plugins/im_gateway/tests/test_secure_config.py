#!/usr/bin/env python3
"""IM Gateway 字段级加密（P0-03）—— secure_config 回归。

运行：
    cd <repo> && python -m unittest plugins.im_gateway.tests.test_secure_config -v

覆盖：加密无明文残留 / 前缀正确 / 读回原文 / 明文读兼容并记 WARNING /
无密钥写抛异常 / 密文损坏读抛异常 / 加密后仍是合法 JSON。
"""
import json
import os
import unittest

from .. import secure_config as sc

_KEY = 'unit-test-encryption-key-0123456789'


class _KeyEnvMixin:
    """统一设置/还原 ENCRYPTION_KEY 并清空 Fernet 缓存。"""

    def setUp(self):
        self._orig = os.environ.get('ENCRYPTION_KEY')
        os.environ['ENCRYPTION_KEY'] = _KEY
        sc._cipher = None

    def tearDown(self):
        if self._orig is None:
            os.environ.pop('ENCRYPTION_KEY', None)
        else:
            os.environ['ENCRYPTION_KEY'] = self._orig
        sc._cipher = None


class EncryptDecryptTest(_KeyEnvMixin, unittest.TestCase):
    """feishu 频道：加密 → 无明文 → 读回原文"""

    SECRET = 'super-secret-app-key-value'

    def test_ciphertext_has_prefix_and_no_plaintext(self):
        enc = sc.encrypt_fields('feishu', {'app_id': 'cli_x', 'app_secret': self.SECRET})
        self.assertTrue(sc.is_encrypted(enc['app_secret']))
        self.assertTrue(enc['app_secret'].startswith('enc:v1:'))
        # 密文不得包含任何明文子串
        self.assertNotIn(self.SECRET, enc['app_secret'])
        self.assertNotIn(self.SECRET, json.dumps(enc))
        # 非敏感字段原样保留
        self.assertEqual(enc['app_id'], 'cli_x')

    def test_encrypted_config_is_still_valid_json(self):
        enc = sc.encrypt_fields('qq', {'app_id': '1024', 'client_secret': 'cs-secret'})
        # 老代码 json.loads 不报错，只是拿到带前缀串
        reloaded = json.loads(json.dumps(enc, ensure_ascii=False))
        self.assertTrue(reloaded['client_secret'].startswith('enc:v1:'))

    def test_decrypt_returns_original(self):
        cfg = {'app_secret': self.SECRET, 'verification_token': 'vtok', 'app_id': 'cli_x'}
        enc = sc.encrypt_fields('feishu', cfg)
        dec = sc.decrypt_fields('feishu', enc)
        self.assertEqual(dec['app_secret'], self.SECRET)
        self.assertEqual(dec['verification_token'], 'vtok')
        self.assertEqual(dec['app_id'], 'cli_x')

    def test_empty_field_is_not_encrypted(self):
        enc = sc.encrypt_fields('wecom', {'secret': '', 'corp_id': 'c1'})
        self.assertEqual(enc['secret'], '')

    def test_encrypt_is_idempotent(self):
        once = sc.encrypt_fields('slack', {'bot_token': 'xoxb-abc'})
        twice = sc.encrypt_fields('slack', once)
        self.assertEqual(once['bot_token'], twice['bot_token'])

    def test_unknown_channel_passthrough(self):
        cfg = {'anything': 'v'}
        self.assertEqual(sc.encrypt_fields('nope', cfg), cfg)
        self.assertEqual(sc.decrypt_fields('nope', cfg), cfg)


class PlaintextCompatTest(_KeyEnvMixin, unittest.TestCase):
    """历史明文读兼容：原样返回并记 WARNING"""

    def test_plaintext_returned_asis_with_warning(self):
        with self.assertLogs('im_gateway.secure_config', level='WARNING') as cm:
            dec = sc.decrypt_fields('line', {'channel_secret': 'legacy-plain'})
        self.assertEqual(dec['channel_secret'], 'legacy-plain')
        self.assertTrue(any('plaintext credential' in m for m in cm.output), cm.output)


class FailClosedTest(_KeyEnvMixin, unittest.TestCase):
    """写缺密钥 / 读坏密文 → 抛异常，绝不降级"""

    def test_write_without_key_raises(self):
        os.environ.pop('ENCRYPTION_KEY', None)
        sc._cipher = None
        with self.assertRaises(sc.SecureConfigUnavailable):
            sc.encrypt_fields('telegram', {'bot_token': '123:abc'})

    def test_empty_write_without_key_is_noop(self):
        os.environ.pop('ENCRYPTION_KEY', None)
        sc._cipher = None
        # 无敏感字段 → 不触碰加密链路，不抛异常
        self.assertEqual(sc.encrypt_fields('telegram', {'webhook_url': 'http://x'})['webhook_url'],
                         'http://x')

    def test_corrupted_ciphertext_raises(self):
        with self.assertRaises(sc.SecureConfigUnavailable):
            sc.decrypt_fields('discord', {'bot_token': 'enc:v1:not-a-valid-fernet-token'})

    def test_corrupt_secret_plaintext_fallback(self):
        # 单值凭据：明文原样返回并记 WARNING
        with self.assertLogs('im_gateway.secure_config', level='WARNING'):
            self.assertEqual(sc.decrypt_secret('legacy'),
                             'legacy')
        with self.assertRaises(sc.SecureConfigUnavailable):
            sc.decrypt_secret('enc:v1:broken')


class LoginSecretTest(_KeyEnvMixin, unittest.TestCase):
    """login_providers.client_secret 单值加解密"""

    def test_roundtrip(self):
        enc = sc.encrypt_secret('oauth-client-secret')
        self.assertTrue(sc.is_encrypted(enc))
        self.assertNotIn('oauth-client-secret', enc)
        self.assertEqual(sc.decrypt_secret(enc), 'oauth-client-secret')

    def test_empty_stays_empty(self):
        self.assertEqual(sc.encrypt_secret(''), '')
        self.assertEqual(sc.decrypt_secret(''), '')


if __name__ == '__main__':
    unittest.main(verbosity=2)