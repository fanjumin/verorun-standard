#!/usr/bin/env python3
"""IM Gateway 入站验签 —— QQ Ed25519 私钥派生的官方向量回归。

运行：
    cd <repo> && python -m unittest plugins.im_gateway.tests.test_webhook_signing -v

背景：`_qq_seed` 曾按 `sha256(secret)` 派生私钥种子，与官方样例对不上，
导致回调地址验证（op=13）和真实事件回调的验签全部失败。官方在「回调地址
验证」文档里给了一组可逐字复算的样例，本文件把它固化成断言 —— 任何人把
派生方式改回 sha256，这里会立刻红。
"""
import hashlib
import unittest

from cryptography.hazmat.primitives.asymmetric import ed25519

from .. import webhook_signing as ws

# 官方文档样例：secret / event_ts / plain_token / 期望签名，四值可直接复算
QQ_SECRET = 'DG5g3B4j9X2KOErG'
QQ_EVENT_TS = '1725442341'
QQ_PLAIN_TOKEN = 'Arq0D5A61EgUu4OxUvOp'
QQ_EXPECTED_SIG = (
    '87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1adc2d01'
    'bcfcadc0842edac85e85205028a1132afe09280305f13aa6909ffc2d652c706'
)


def _sign_with_seed(seed: bytes, message: str) -> str:
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed).sign(
        message.encode('utf-8')).hex()


def _qq_headers(ts: str, body: str, seed: bytes = None) -> dict:
    seed = seed if seed is not None else ws._qq_seed(QQ_SECRET)
    return {
        'X-Signature-Ed25519': _sign_with_seed(seed, ts + body),
        'X-Signature-Timestamp': ts,
    }


class QqSeedTest(unittest.TestCase):
    """私钥种子派生：secret 反复拼接至 32 字节后截断"""

    def test_seed_repeats_secret_to_32_bytes(self):
        self.assertEqual(ws._qq_seed(QQ_SECRET),
                         b'DG5g3B4j9X2KOErGDG5g3B4j9X2KOErG')

    def test_seed_is_not_sha256_of_secret(self):
        self.assertNotEqual(ws._qq_seed(QQ_SECRET),
                            hashlib.sha256(QQ_SECRET.encode()).digest())

    def test_secret_longer_than_32_bytes_is_truncated(self):
        self.assertEqual(ws._qq_seed('A' * 40), b'A' * 32)

    def test_empty_secret_raises(self):
        with self.assertRaises(ValueError):
            ws._qq_seed('')


class QqOfficialVectorTest(unittest.TestCase):
    """用官方样例断言签名结果逐字一致"""

    def test_op13_signature_matches_official_vector(self):
        resp = ws.build_qq_validation_response(QQ_SECRET, QQ_PLAIN_TOKEN, QQ_EVENT_TS)
        self.assertEqual(resp['plain_token'], QQ_PLAIN_TOKEN)
        self.assertEqual(resp['signature'], QQ_EXPECTED_SIG)

    def test_official_signature_verifies_against_derived_public_key(self):
        resp = ws.build_qq_validation_response(QQ_SECRET, QQ_PLAIN_TOKEN, QQ_EVENT_TS)
        ws._qq_public_key(QQ_SECRET).verify(
            bytes.fromhex(resp['signature']),
            (QQ_EVENT_TS + QQ_PLAIN_TOKEN).encode('utf-8'))


class QqVerifyTest(unittest.TestCase):
    """事件回调验签：签名覆盖 (timestamp + body)，无 body 完整性缺口"""

    BODY = '{"op":0,"t":"GROUP_AT_MESSAGE_CREATE","d":{"content":" hi "}}'

    def test_accepts_genuine_signature(self):
        self.assertEqual(ws.verify_qq(_qq_headers(QQ_EVENT_TS, self.BODY),
                                      self.BODY, QQ_SECRET), (True, ''))

    def test_rejects_signature_from_sha256_derivation(self):
        # 修复前的实现正是这种派生，会被平台真实签名判为非法
        bad = _qq_headers(QQ_EVENT_TS, self.BODY,
                          hashlib.sha256(QQ_SECRET.encode()).digest())
        self.assertEqual(ws.verify_qq(bad, self.BODY, QQ_SECRET),
                         (False, 'invalid qq signature'))

    def test_rejects_tampered_body(self):
        ok, reason = ws.verify_qq(_qq_headers(QQ_EVENT_TS, self.BODY),
                                  self.BODY.replace('hi', 'pay'), QQ_SECRET)
        self.assertFalse(ok)
        self.assertEqual(reason, 'invalid qq signature')

    def test_rejects_replayed_timestamp_with_wrong_body(self):
        # timestamp 参与签名，改 ts 即失效（不是无关紧要的装饰字段）
        hdrs = _qq_headers(QQ_EVENT_TS, self.BODY)
        hdrs['X-Signature-Timestamp'] = '1725442342'
        self.assertFalse(ws.verify_qq(hdrs, self.BODY, QQ_SECRET)[0])

    def test_missing_secret_fails_closed(self):
        self.assertEqual(ws.verify_qq({}, self.BODY, ''),
                         (False, 'qq bot_secret not configured'))

    def test_missing_headers_fails_closed(self):
        self.assertEqual(ws.verify_qq({}, self.BODY, QQ_SECRET),
                         (False, 'missing qq signature headers'))

    def test_malformed_signature_hex_is_rejected_not_raised(self):
        hdrs = {'X-Signature-Ed25519': 'zzzz', 'X-Signature-Timestamp': QQ_EVENT_TS}
        ok, reason = ws.verify_qq(hdrs, self.BODY, QQ_SECRET)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith('qq verify error'), reason)


class QqDispatchTest(unittest.TestCase):
    """verify() 的 qq 分支：密钥取自 channel_configs.bot_secret / client_secret"""

    BODY = '{"op":0,"t":"C2C_MESSAGE_CREATE","d":{"content":"hi"}}'

    def setUp(self):
        self._orig = ws._load_channel_config

    def tearDown(self):
        ws._load_channel_config = self._orig

    def test_uses_bot_secret(self):
        ws._load_channel_config = lambda channel: {'bot_secret': QQ_SECRET}
        self.assertEqual(ws.verify('qq', _qq_headers(QQ_EVENT_TS, self.BODY), self.BODY),
                         (True, ''))

    def test_falls_back_to_client_secret(self):
        ws._load_channel_config = lambda channel: {'client_secret': QQ_SECRET}
        self.assertEqual(ws.verify('qq', _qq_headers(QQ_EVENT_TS, self.BODY), self.BODY),
                         (True, ''))

    def test_unconfigured_channel_fails_closed(self):
        ws._load_channel_config = lambda channel: {}
        self.assertEqual(ws.verify('qq', _qq_headers(QQ_EVENT_TS, self.BODY), self.BODY),
                         (False, 'qq bot_secret not configured'))


class OtherChannelsUnaffectedTest(unittest.TestCase):
    """改动只碰 QQ；顺手锚住 line 分支，避免以后共用的 hashlib 被误删"""

    def setUp(self):
        self._orig = ws._load_channel_config

    def tearDown(self):
        ws._load_channel_config = self._orig

    def test_line_still_hmac_sha256_of_body(self):
        import base64
        import hmac

        secret, body = 'line-secret', '{"events":[]}'
        ws._load_channel_config = lambda channel: {'channel_secret': secret}
        digest = base64.b64encode(
            hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(ws.verify('line', {'X-Line-Signature': digest}, body),
                         (True, ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
