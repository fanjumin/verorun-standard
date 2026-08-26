# VeroRun Standard Edition

本仓库由 `sync-to-standard` CI 流水线自动生成，内容为 VeroRun 公共内核（**不包含任何插件**）。请勿手动推送本仓库。

---

## English

## Overview

The Standard Edition is the free, public core of VeroRun. It ships **no built-in plugins** — every plugin is installed on demand from the built-in plugin store.

## Quick Start

```bash
# Deploy the standard runtime (Ubuntu 22.04+, Python 3.11+, PostgreSQL)
sudo env INSTALL_TYPE=website bash deploy/install.sh install
```

After install:

1. Open the admin console and activate your edition (Pro / Edu / MiniPro / Edge) to unlock its bundled plugin entitlements.
2. Install plugins from **Admin → Plugins → Store**.
3. Third-party / paid plugins require an active subscription or license.

## Versioning

- Distributed exclusively by the `sync-to-standard` CI pipeline, triggered when an allowlisted release tag (`v*`) is pushed to `verorun-code`.
- Standard ships the core only; plugin entitlements follow the activated edition.

---

## 中文

## 概述

标准版是 VeroRun 免费公开的内核发行版。**不内置任何插件** —— 所有插件均通过内置插件商店按需安装。

## 快速开始

```bash
# 部署标准运行时（Ubuntu 22.04+ / Python 3.11+ / PostgreSQL）
sudo env INSTALL_TYPE=website bash deploy/install.sh install
```

安装完成后：

1. 打开管理后台，激活对应版本（Pro / Edu / MiniPro / Edge），解锁该版本的内置插件授权。
2. 在 **管理后台 → 插件 → 商店** 安装插件。
3. 第三方 / 付费插件需要有效的订阅或 License。

## 版本说明

- 本仓库仅由 `sync-to-standard` CI 流水线分发：向 `verorun-code` 推送白名单 tag（`v*`）时自动触发。
- 标准版只含内核；插件授权随激活版本而定。
