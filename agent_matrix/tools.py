#!/usr/bin/env python3
"""
Agent Matrix — 工具注册中心 (Tool Registry)
===========================================
为子 Agent 提供原生 function calling 可调用的工具集。

设计原则：
  - 首批仅内置「只读、安全」工具，不给写库/删除类能力。
  - 工具执行统一带 try/except 兜底，失败返回字符串错误信息而非抛异常，
    保证 ReAct 循环不会因单个工具出错而崩溃。
  - 按 Agent 的 allowed_tools 白名单过滤，未授权工具不下发给模型；
    execute_tool 传入 allowed_tools 时同一白名单在执行层再强制一次
    （A4：此前白名单只过滤 schema，不过滤实际执行，存在提示注入绕过风险）。

对外接口：
  - get_tools_for_agent(allowed_tools) -> list[schema]
  - execute_tool(name, args, allowed_tools=None) -> str
"""
from i18n import _
import json, os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
# WP-8：spawn_subagent 工具实现体（内部延迟导入 tools/models，无循环依赖）
from agent_matrix.tools_subagent import _tool_spawn_subagent

logger = logging.getLogger(__name__)

# ============================================================
# 工具 Schema（OpenAI function calling 格式）
# ============================================================

TOOL_SCHEMAS = {
    "get_system_health": {
        "type": "function",
        "function": {
            "name": "get_system_health",
            "description": "Get a summary of the latest system health inspection, including health score, passed/warning/error counts, and per-check status. Use for questions about system status, service health, and alerts.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "query_stats": {
        "type": "function",
        "function": {
            "name": "query_stats",
            "description": "Query the site traffic statistics report (PV/UV/sessions/trends/sources/top pages) and return readable text insights. Use for questions about traffic, visits, and data trends.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Statistics period in days, default 7",
                        "default": 7
                    }
                },
                "required": []
            }
        }
    },
    "search_knowledge": {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the platform knowledge base for content fragments related to a keyword. Use for questions about product features, FAQ, and usage help.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword"
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    "ads_list": {
        "type": "function",
        "function": {
            "name": "ads_list",
            "description": "List ad zones in the ads management system; filter by site, position, and active state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "site_key": {"type": "string", "description": "Site key, default default"},
                    "position": {"type": "string", "description": "Ad position, e.g. sidebar"},
                    "active_only": {"type": "boolean", "description": "Only return active ad zones", "default": False}
                },
                "required": []
            }
        }
    },
    "ads_create": {
        "type": "function",
        "function": {
            "name": "ads_create",
            "description": "Create a new ad zone. Supports image ads or ad code, with delivery position, schedule, targeting rules, weight, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Ad name (required)"},
                    "site_key": {"type": "string", "description": "Site key, default default"},
                    "zone_id": {"type": "integer", "description": "Ad zone ID, default 0"},
                    "position": {"type": "string", "description": "Ad position key, default sidebar"},
                    "page": {"type": "string", "description": "Display page, default * means the whole site"},
                    "ad_type": {"type": "string", "enum": ["image", "code"], "description": "Ad type"},
                    "image_url": {"type": "string", "description": "Image ad URL"},
                    "link_url": {"type": "string", "description": "Image ad click-through link"},
                    "ad_code": {"type": "string", "description": "Ad code (HTML/JS)"},
                    "width": {"type": "integer", "description": "Width (px)"},
                    "height": {"type": "integer", "description": "Height (px)"},
                    "targeting_rules": {"type": "object", "description": "Targeting rules JSON object"},
                    "schedule_start": {"type": "string", "description": "Delivery start time, ISO format"},
                    "schedule_end": {"type": "string", "description": "Delivery end time, ISO format"},
                    "weight": {"type": "integer", "description": "Weight, default 1"},
                    "freq_cap": {"type": "integer", "description": "Per-user daily frequency cap, 0 = unlimited"},
                    "click_tag": {"type": "string", "description": "Click tracking tag"},
                    "utm_source": {"type": "string", "description": "UTM source"},
                    "is_active": {"type": "integer", "description": "Active flag, 1=enabled, 0=disabled"},
                    "sort_order": {"type": "integer", "description": "Sort order, default 0"}
                },
                "required": ["name"]
            }
        }
    },
    "ads_update": {
        "type": "function",
        "function": {
            "name": "ads_update",
            "description": "Update fields of an ad zone, e.g. enable/disable, change code, adjust weight.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ad_id": {"type": "integer", "description": "Ad ID"},
                    "updates": {"type": "object", "description": "Key-value pairs of fields to update; field meanings same as ads_create"}
                },
                "required": ["ad_id", "updates"]
            }
        }
    },
    "ads_delete": {
        "type": "function",
        "function": {
            "name": "ads_delete",
            "description": "Delete an ad zone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ad_id": {"type": "integer", "description": "Ad ID"}
                },
                "required": ["ad_id"]
            }
        }
    },
    "ads_get_stats": {
        "type": "function",
        "function": {
            "name": "ads_get_stats",
            "description": "Query ad statistics, including impressions, clicks, CTR, and daily trends.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ad_id": {"type": "integer", "description": "Ad ID; omit to aggregate all ads"},
                    "days": {"type": "integer", "description": "Statistics period in days, default 7", "default": 7}
                },
                "required": []
            }
        }
    },
    "ads_analyze": {
        "type": "function",
        "function": {
            "name": "ads_analyze",
            "description": "Analyze ad performance; return text insights (high clicks, low CTR, trends) and optimization suggestions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Statistics period in days, default 7", "default": 7}
                },
                "required": []
            }
        }
    },
    "ads_render_snippet": {
        "type": "function",
        "function": {
            "name": "ads_render_snippet",
            "description": "Generate a Jinja2 template snippet that renders an ad zone at a given page position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "position": {"type": "string", "description": "Ad position key, default sidebar"},
                    "page": {"type": "string", "description": "Display page, default *"},
                    "site_key": {"type": "string", "description": "Site key, default default"},
                    "zone_id": {"type": "integer", "description": "Ad zone ID"}
                },
                "required": ["position"]
            }
        }
    },
    "generate_ppt": {
        "type": "function",
        "function": {
            "name": "generate_ppt",
            "description": "Generate a PowerPoint presentation (.pptx) with AI. Provide topic, page count, and style to produce a downloadable PPT file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "PPT topic (required)"},
                    "pages": {"type": "integer", "description": "Page count, default 10, range 3-20", "default": 10},
                    "style": {"type": "string", "description": "Style description, e.g. 'dark tech style', 'minimal business', 'education style'", "default": "Dark Tech Style, 16:9"}
                },
                "required": ["topic"]
            }
        }
    },
    "generate_image": {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate images with AI. Provide a text description; style and count are optional.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Image description (required)"},
                    "style": {"type": "string", "description": "Style, one of realistic/anime/3d/painting/line-art", "default": "realistic"},
                    "count": {"type": "integer", "description": "Number of images (1-4), default 1", "default": 1},
                    "size": {"type": "string", "description": "Size, one of 1024x1024/1792x1024/1024x1792, default 1024x1024", "default": "1024x1024"}
                },
                "required": ["prompt"]
            }
        }
    },
    "generate_markdown": {
        "type": "function",
        "function": {
            "name": "generate_markdown",
            "description": "Generate a Markdown document with AI. Produce a previewable/downloadable .md file from topic and format requirements. Supports articles, reports, technical docs, notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Document topic (required)"},
                    "outline": {"type": "string", "description": "Outline requirements, optional sections or key points"},
                    "style": {"type": "string", "description": "Style, one of technical/professional/creative/simple/list", "default": "technical"}
                },
                "required": ["topic"]
            }
        }
    },
    "generate_docx": {
        "type": "function",
        "function": {
            "name": "generate_docx",
            "description": "Generate a styled Word document (.docx) with AI from topic and format requirements, including headings, paragraphs, lists, and tables. Suits reports, contracts, proposals, resumes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Document topic (required)"},
                    "style": {"type": "string", "description": "Style, one of professional/formal/creative/simple/report", "default": "professional"},
                    "sections": {"type": "integer", "description": "Number of sections (3-12), default 5", "default": 5}
                },
                "required": ["topic"]
            }
        }
    },
    "cms_list_channels": {
        "type": "function",
        "function": {
            "name": "cms_list_channels",
            "description": "List all available publishing channels (categories) in the CMS, to decide where articles can be published.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    "cms_create_post": {
        "type": "function",
        "function": {
            "name": "cms_create_post",
            "description": "Create a new article in the CMS. It starts as a draft; call cms_publish_post to publish it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Article title (required)"},
                    "content": {"type": "string", "description": "Article body content, HTML supported"},
                    "excerpt": {"type": "string", "description": "Article excerpt/lead"},
                    "category": {"type": "string", "description": "Channel/category key, e.g. insights, news"},
                    "cover_image": {"type": "string", "description": "Cover image URL"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tag list"}
                },
                "required": ["title"]
            }
        }
    },
    "cms_publish_post": {
        "type": "function",
        "function": {
            "name": "cms_publish_post",
            "description": "Publish a CMS article to selected channels: local categories and/or social media platforms.",
            "parameters": {
                "type": "object",
                "properties": {
                    "post_id": {"type": "integer", "description": "Article ID (required)"},
                    "channels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Target channel list, format 'local:channel_name', e.g. ['local:insights']"
                    }
                },
                "required": ["post_id", "channels"]
            }
        }
    },
    "spawn_subagent": {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Dispatch a sub-task to a specialized sub-agent and return its final result as text. Use when the current task needs a different domain specialist (research, writing, data analysis, etc.). The sub-agent runs with its own isolated token budget and may use only tools shared between the parent's whitelist and its own role whitelist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Sub-agent identifier (slug or name)"},
                    "task": {"type": "string", "description": "Task description for the sub-agent (required)"},
                    "expected_output": {"type": "string", "description": "Optional expected output format"}
                },
                "required": ["agent", "task"]
            }
        }
    },
}


# ============================================================
# 工具执行器（均为只读）
# ============================================================

def _get_matrix_db():
    """获取 agent_matrix 所在主库连接（复用项目 models.get_db）"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from models import get_db
    return get_db()


def _tool_get_system_health(args):
    """读取最近一次健康巡检结果汇总（只读）"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from shared.plugin_access import get_attr
        health_db = get_attr('plugins.health_check.models', 'get_db',
                             feature='tool_system_health')
        if health_db is None:
            return "No health inspection records."
        with health_db() as conn:
            run = conn.execute(
                "SELECT * FROM check_runs WHERE status='completed' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not run:
                return "No health inspection records."
            run = dict(run)
            total = run.get('total_checks', 0) or 0
            passed = run.get('passed', 0) or 0
            warnings = run.get('warnings', 0) or 0
            errors = run.get('errors', 0) or 0
            denom = passed + warnings + errors
            score = round((passed + warnings * 0.5) * 100 / denom, 1) if denom else 100.0

            items = conn.execute(
                "SELECT check_name, status, message FROM check_history "
                "WHERE run_id=%s ORDER BY status DESC",
                (run['id'],)
            ).fetchall()
        lines = [
            f"Health Score: {score}/100",
            f"Total Checks: {total}, Passed: {passed}, Warnings: {warnings}, Errors: {errors}",
            f"Inspection Time: {run.get('created_at', '')}",
        ]
        abnormal = [dict(i) for i in items if i['status'] != 'passed']
        if abnormal:
            lines.append("Abnormal items:")
            for i in abnormal[:15]:
                lines.append(f"  - [{i['status']}] {i['check_name']}: {(i['message'] or '')[:80]}")
        else:
            lines.append("All checks passed.")
        return '\n'.join(lines)
    except Exception as e:
        logger.warning(f"[tool:get_system_health] 执行失败: {e}")
        return f"Failed to Get Health Status: {e}"


def _tool_query_stats(args):
    """生成站点统计报告的文字洞察（只读）"""
    try:
        days = int(args.get('days', 7) or 7)
        days = max(1, min(days, 90))
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from shared.plugin_access import get_attr
        generate_report = get_attr('plugins.analytics.tracker', 'generate_report',
                                   feature='tool_query_stats')
        generate_insight_text = get_attr('plugins.analytics.tracker', 'generate_insight_text',
                                         feature='tool_query_stats')
        if generate_report is None or generate_insight_text is None:
            return "Failed to Query Data Statistics: analytics plugin is not installed"
        report = generate_report(days=days)
        return generate_insight_text(report)
    except Exception as e:
        logger.warning(f"[tool:query_stats] 执行失败: {e}")
        return f"Failed to Query Data Statistics: {e}"


def _tool_search_knowledge(args):
    """在知识库中检索关键词（只读）"""
    try:
        keyword = str(args.get('keyword', '')).strip()
        if not keyword:
            return "No search keyword provided."
        with _get_matrix_db() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key='chatbot_knowledge_base'"
            ).fetchone()
        content = (row['value'] if row and row['value'] else '') or ''
        if not content:
            return "The knowledge base is empty."
        # 简单按段落匹配，返回命中片段
        blocks = [b.strip() for b in content.split('\n\n') if b.strip()]
        hits = [b for b in blocks if keyword.lower() in b.lower()]
        if not hits:
            return f"No content related to 「{keyword}」 found in the knowledge base."
        return '\n---\n'.join(hits[:5])[:2000]
    except Exception as e:
        logger.warning(f"[tool:search_knowledge] 执行失败: {e}")
        return f"Failed to retrieve knowledge base: {e}"


def _tool_ads_list(args):
    """列出广告"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_list')
        if ads_tools is None:
            return "Failed to Get Ad List: ads plugin is not installed"
        res = ads_tools.list_ads(
            site_key=args.get('site_key'),
            position=args.get('position'),
            active_only=args.get('active_only', False)
        )
        if not res['success']:
            return f"Failed to Get Ad List: {res.get('error')}"
        ads = res.get('data', [])
        if not ads:
            return "No ad space."
        lines = [f"Total {len(ads)} Ad Positions:"]
        for a in ads:
            status = 'Enabled' if a.get('is_active') else 'Disabled'
            lines.append(
                f"ID {a['id']}: {a['name']} | Site {a.get('site_key','default')} |"
                f"Location {a.get('position','-')} | Type {a.get('ad_type','image')} | {status}"
            )
        return '\n'.join(lines)
    except Exception as e:
        logger.warning(f"[tool:ads_list] 执行失败: {e}")
        return f"Failed to Get Ad List: {e}"


def _tool_ads_create(args):
    """创建广告"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_create')
        if ads_tools is None:
            return "Ad creation failed: ads plugin is not installed"
        res = ads_tools.create_ad(args)
        if res['success']:
            return f"✅ Ad created, ID: {res['data']['id']}"
        return f"❌ Creation Failed: {res.get('error')}"
    except Exception as e:
        logger.warning(f"[tool:ads_create] 执行失败: {e}")
        return f"Ad creation failed: {e}"


def _tool_ads_update(args):
    """更新广告"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_update')
        if ads_tools is None:
            return "Failed to update ad: ads plugin is not installed"
        ad_id = args.get('ad_id')
        updates = args.get('updates', {})
        res = ads_tools.update_ad(ad_id, updates)
        if res['success']:
            return f"✅ Ad {ad_id} updated"
        return f"❌ Update Failed: {res.get('error')}"
    except Exception as e:
        logger.warning(f"[tool:ads_update] 执行失败: {e}")
        return f"Failed to update ad: {e}"


def _tool_ads_delete(args):
    """删除广告"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_delete')
        if ads_tools is None:
            return "Failed to Delete Ad: ads plugin is not installed"
        res = ads_tools.delete_ad(args.get('ad_id'))
        if res['success']:
            return f"✅ Ad {args.get('ad_id')} deleted"
        return f"❌ Deletion Failed: {res.get('error')}"
    except Exception as e:
        logger.warning(f"[tool:ads_delete] 执行失败: {e}")
        return f"Failed to Delete Ad: {e}"


def _tool_ads_get_stats(args):
    """查询广告统计"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_get_stats')
        if ads_tools is None:
            return "Failed to Query Ad Statistics: ads plugin is not installed"
        res = ads_tools.get_stats(
            ad_id=args.get('ad_id'),
            site_key=args.get('site_key'),
            days=int(args.get('days', 7))
        )
        if not res['success']:
            return f"Query Statistics Failed: {res.get('error')}"
        data = res.get('data', {})
        total = data.get('total', {})
        daily = data.get('daily', [])
        lines = [
            f"=== Advertising Statistics (Last {args.get('days',7)} Days) ===",
            f"Impressions: {total.get('impressions', 0)}",
            f"Clicks: {total.get('clicks', 0)}",
            f"CTR: {total.get('ctr', 0)}%",
        ]
        if daily:
            lines.append("Daily trend:")
            for r in daily[-10:]:
                lines.append(f"  {r['stat_date']}: Impressions {r.get('impressions',0)} Clicks {r.get('clicks',0)}")
        return '\n'.join(lines)
    except Exception as e:
        logger.warning(f"[tool:ads_get_stats] 执行失败: {e}")
        return f"Failed to Query Ad Statistics: {e}"


def _tool_ads_analyze(args):
    """分析广告效果"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_analyze')
        if ads_tools is None:
            return "Advertisement analysis failed: ads plugin is not installed"
        res = ads_tools.analyze_ads(days=int(args.get('days', 7)))
        if res['success']:
            return res['data']
        return f"Analysis failed: {res.get('error')}"
    except Exception as e:
        logger.warning(f"[tool:ads_analyze] 执行失败: {e}")
        return f"Advertisement analysis failed: {e}"


def _tool_ads_render_snippet(args):
    """生成广告渲染代码片段"""
    try:
        from shared.plugin_access import optional_import
        ads_tools = optional_import('plugins.ads.ai_tools', feature='tool_ads_render_snippet')
        if ads_tools is None:
            return "Failed to generate code: ads plugin is not installed"
        res = ads_tools.generate_render_snippet(
            position=args.get('position', 'sidebar'),
            page=args.get('page', '*'),
            site_key=args.get('site_key', 'default'),
            zone_id=args.get('zone_id')
        )
        if res['success']:
            return "Add the following snippet to the template to render the ad zone:\n```jinja2\n" + res['data'] + "\n```"
        return f"Failed to generate code: {res.get('error')}"
    except Exception as e:
        logger.warning(f"[tool:ads_render_snippet] 执行失败: {e}")
        return f"Failed to generate ad rendering code: {e}"


def _tool_cms_list_channels(args):
    """列出 CMS 频道"""
    try:
        from models import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, name, slug, description FROM cms_categories WHERE is_active=1 ORDER BY sort_order"
            ).fetchall()
        if not rows:
            return "No available channels."
        lines = ["Available CMS channels:"]
        for r in rows:
            r = dict(r)
            lines.append(f"  ID {r['id']}: {r['name']} (slug: {r['slug']})")
        return '\n'.join(lines)
    except Exception as e:
        logger.warning(f"[tool:cms_list_channels] Failed: {e}")
        return f"Failed to get channel list: {e}"


def _tool_cms_create_post(args):
    """创建 CMS 文章"""
    try:
        title = str(args.get('title', '')).strip()
        if not title:
            return "Error: Article title is required."

        import uuid
        post_data = {
            'title': title,
            'content': str(args.get('content', '')),
            'excerpt': str(args.get('excerpt', '')),
            'category': str(args.get('category', 'insights')),
            'cover_image': str(args.get('cover_image', '')),
            'tags': args.get('tags', []),
            'slug': 'article-' + str(uuid.uuid4())[:8],
            'source': 'ai',
            'is_published': 0,
        }

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'auth-center'))
        from models.cms import upsert_post
        result = upsert_post(post_data)
        post_id = result.get('id') if isinstance(result, dict) else result
        return f"Article created, ID: {post_id}, Title: {title}"
    except Exception as e:
        logger.warning(f"[tool:cms_create_post] Failed: {e}")
        return f"Failed to create article: {e}"


def _tool_cms_publish_post(args):
    """发布 CMS 文章"""
    try:
        post_id = args.get('post_id')
        channels = args.get('channels', [])

        if not post_id:
            return "Error: post_id is required."
        if not channels:
            return "Error: channels is required."

        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM cms_posts WHERE id=%s", (post_id,)
            ).fetchone()
        if not row:
            return f"Error: Article {post_id} not found."

        # Separate local channels
        local_cats = []
        for ch in channels:
            if ch.startswith('local:'):
                local_cats.append(ch.split(':', 1)[1])

        if local_cats:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'auth-center'))
            from models.cms import upsert_post
            post = dict(row)
            upsert_post({
                'id': post_id,
                'slug': post['slug'],
                'category': local_cats[0],
                'title': post['title'],
                'excerpt': post.get('excerpt', ''),
                'content': post['content'],
                'is_published': 1,
                'publish_channels': channels,
            })

        return f"Article {post_id} published to: {', '.join(local_cats)}"
    except Exception as e:
        logger.warning(f"[tool:cms_publish_post] Failed: {e}")
        return f"Failed to publish article: {e}"


def _tool_generate_ppt(args):
    """使用 AI 生成 PPT 文件"""
    try:
        topic = str(args.get('topic', 'Untitled topic')).strip()
        if not topic:
            return '❌ Please provide the PPT topic'
        pages = max(3, min(int(args.get('pages', 10) or 10), 20))
        style = str(args.get('style', 'Dark Tech Style, 16:9'))
        from agent_matrix.routes import _generate_ppt_file
        filename = _generate_ppt_file(topic, pages, style)
        if filename:
            return f'✅ PPT generated: "{topic}" ({pages} pages)\nDownload link: /admin/agent-matrix/media/download/{filename}'
        return '❌ PPT generation failed, check the backend logs'
    except Exception as e:
        logger.warning(f"[tool:generate_ppt] 执行失败: {e}")
        return f'❌ PPT Generation Error: {e}'


def _tool_generate_image(args):
    """使用硅基流动 API 生成图像"""
    try:
        prompt = str(args.get('prompt', '')).strip()
        if not prompt:
            return '❌ Please provide the image description'
        count = max(1, min(int(args.get('count', 1) or 1), 4))
        size = str(args.get('size', '1024x1024'))
        if '\\' in size:
            size = '1024x1024'

        # 从 system_config 读取 siliconflow_api_key
        from models import get_db
        with get_db() as conn:
            row = conn.execute("SELECT value FROM system_config WHERE key='siliconflow_api_key'").fetchone()
        api_key = row['value'] if row else os.environ.get('SILICONFLOW_API_KEY', '')
        if not api_key:
            return '❌ SiliconFlow API key is not configured'

        # 风格 → 模型映射
        style_map = {
            'realistic': 'black-forest-labs/FLUX.1-dev',
            'anime': 'stabilityai/stable-diffusion-xl-base-1.0',
            '3d': 'stabilityai/stable-diffusion-xl-base-1.0',
            'painting': 'SG161222/RealVisXL_V4.0',
            'line-art': 'lykon/dreamshaper-xl-1-0',
        }
        model = style_map.get(args.get('style', 'realistic'), 'black-forest-labs/FLUX.1-dev')

        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url='https://api.siliconflow.cn/v1')
        resp = client.images.generate(
            model=model,
            prompt=prompt,
            n=count,
            size=size
        )

        # 保存图片到 media 目录
        import uuid, requests
        media_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'media', 'temp')
        os.makedirs(media_dir, exist_ok=True)

        urls = []
        for i, img_data in enumerate(resp.data):
            img_url = img_data.url
            if not img_url:
                continue
            r = requests.get(img_url, timeout=30)
            ext = 'png'
            fn = f"img_{uuid.uuid4().hex[:8]}_{i}.{ext}"
            fp = os.path.join(media_dir, fn)
            with open(fp, 'wb') as f:
                f.write(r.content)
            download_url = f'/admin/agent-matrix/media/download/{fn}'
            urls.append(download_url)

        if not urls:
            return '❌ Image generation API returned empty'

        lines = [f'✅ Generated {len(urls)} images:']
        for u in urls:
            lines.append(f'  {u}')
        return '\n'.join(lines)

    except Exception as e:
        logger.warning(f"[tool:generate_image] 执行失败: {e}")
        return f'❌ Image Generation Error: {e}'


def _tool_generate_markdown(args):
    """使用 AI 生成 Markdown 文档"""
    try:
        topic = str(args.get('topic', '')).strip()
        if not topic:
            return '❌ Please provide the document topic'
        outline = str(args.get('outline', ''))
        style = str(args.get('style', 'technical'))

        style_prompt = {
            'technical': 'Technical document style: clear structure, well-defined hierarchy, professional terms in English',
            'professional': 'Business report style: formal language, complete paragraphs, clear conclusions',
            'creative': 'Creative writing style: lively and persuasive language',
            'simple': 'Concise style with bullet points for quick reading',
            'list': 'List style: mainly items and sub-items',
        }.get(style, 'Technical document style: clear structure, well-defined hierarchy, professional terms in English')

        prompt_text = f'Write a Markdown document about "{topic}".\nStyle requirements: {style_prompt}'
        if outline:
            prompt_text += f'\nOutline requirements: {outline}'
        prompt_text += '\nOutput Markdown content directly, including headings (#), paragraphs, lists, code blocks, and other formatting elements.'

        from agent_matrix.engine import get_gateway
        from agent_matrix.model_resolver import resolve_model_args
        gw = get_gateway()
        content = gw.chat(
            messages=[
                {'role': 'system', 'content': 'You are a professional document writing assistant. Output only Markdown content, without any additional explanations.'},
                {'role': 'user', 'content': prompt_text}
            ],
            temperature=0.5,
            max_tokens=4096,
            module='tools.generate_markdown',
            **resolve_model_args({}),
        )
        if not content.strip():
            return '❌ AI content generation returned empty, please retry'

        # 保存为 .md 文件
        import uuid
        media_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'media', 'temp')
        os.makedirs(media_dir, exist_ok=True)
        fn = f"md_{uuid.uuid4().hex[:8]}.md"
        fp = os.path.join(media_dir, fn)
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(content)

        download_url = f'/admin/agent-matrix/media/download/{fn}'
        preview_url = f'/admin/agent-matrix/md-preview/{fn}'
        preview = content[:200].strip().replace('\n', ' ')
        return f'✅ Document generated: "{topic}"\nPreview: {preview}...\nFile: {fn}\nPreview link: {preview_url}\nDownload link: {download_url}'

    except Exception as e:
        logger.warning(f"[tool:generate_markdown] 执行失败: {e}")
        return f'❌ Markdown Generation Error: {e}'


def _tool_generate_docx(args):
    """使用 AI 生成 Word 文档（.docx）"""
    try:
        topic = str(args.get('topic', '')).strip()
        if not topic:
            return '❌ Please provide the document topic'
        sections = max(3, min(int(args.get('sections', 5) or 5), 12))
        style = str(args.get('style', 'professional'))

        style_desc = {
            'professional': 'Professional business style: formal titles and paragraph formats',
            'formal': 'Formal official-document style: clear chapters and sections, rigorous language',
            'creative': 'Creative design style: flexible layouts and a modern feel',
            'simple': 'Concise style with highlighted key points for quick reading',
            'report': 'Report style: includes summary, data analysis, conclusions, and recommendations',
        }.get(style, 'Professional business style: formal titles and paragraph formats')

        prompt_text = f'Write a Word document about "{topic}" with {sections} sections.\nStyle requirements: {style_desc}\nOutput Markdown format with headings (##), paragraphs, and lists.'

        from agent_matrix.engine import get_gateway
        from agent_matrix.model_resolver import resolve_model_args
        gw = get_gateway()
        md_content = gw.chat(
            messages=[
                {'role': 'system', 'content': 'You are a professional document writing assistant. Output structured Markdown content with multiple levels of headings and paragraphs, without any additional explanations.'},
                {'role': 'user', 'content': prompt_text}
            ],
            temperature=0.5,
            max_tokens=4096,
            module='tools.generate_docx',
            **resolve_model_args({}),
        )
        if not md_content.strip():
            return '❌ AI content generation returned empty'

        # 用 python-docx 渲染
        from docx import Document
        from docx.shared import Pt, Inches, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        doc = Document()

        # 设置默认字体
        style_n = doc.styles['Normal']
        style_n.font.name = 'Microsoft YaHei'
        style_n.font.size = Pt(11)
        style_n.paragraph_format.space_after = Pt(6)

        # 标题
        lines = md_content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('## '):
                h = doc.add_heading(line[3:], level=2)
                for run in h.runs:
                    run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)
                    run.font.size = Pt(16)
            elif line.startswith('### '):
                h = doc.add_heading(line[4:], level=3)
                for run in h.runs:
                    run.font.color.rgb = RGBColor(0x2D, 0x2D, 0x44)
                    run.font.size = Pt(13)
            elif line.startswith('- ') or line.startswith('* '):
                p = doc.add_paragraph(line[2:], style='List Bullet')
            elif line.startswith('1. ') or line.startswith('2. ') or any(line.startswith(f'{i}. ') for i in range(1, 10)):
                p = doc.add_paragraph(line, style='List Number')
            else:
                p = doc.add_paragraph(line)

        import uuid
        media_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'media', 'temp')
        os.makedirs(media_dir, exist_ok=True)
        fn = f"doc_{uuid.uuid4().hex[:8]}.docx"
        fp = os.path.join(media_dir, fn)
        doc.save(fp)

        download_url = f'/admin/agent-matrix/media/download/{fn}'
        return f'✅ Word document generated: "{topic}" ({sections} sections)\nFile: {fn}\nDownload link: {download_url}'

    except Exception as e:
        logger.warning(f"[tool:generate_docx] 执行失败: {e}")
        return f'❌ Word Document Generation Error: {e}'


# ============================================================
# WP-2：工具并发元数据（独立注册表，不进发往 API 的 schema）
# ============================================================
# concurrency_safe=True 仅授予只读查询类工具；写工具/生成类工具默认
# False（保守起步：并发安全必须显式声明，未声明即按不安全处理）。未注册的
# 工具（含 MCP 合并工具）一律按 (False, True) 处理。
TOOL_CONCURRENCY = {
    'get_system_health': {'concurrency_safe': True, 'interruptible': True},
    'query_stats': {'concurrency_safe': True, 'interruptible': True},
    'search_knowledge': {'concurrency_safe': True, 'interruptible': True},
    'ads_list': {'concurrency_safe': True, 'interruptible': True},
    'ads_get_stats': {'concurrency_safe': True, 'interruptible': True},
    'ads_analyze': {'concurrency_safe': True, 'interruptible': True},
    'ads_render_snippet': {'concurrency_safe': True, 'interruptible': True},
    'cms_list_channels': {'concurrency_safe': True, 'interruptible': True},
    # WP-8：子代理为同步长任务，不套超时线程（避免超时后后台线程重复执行）
    'spawn_subagent': {'concurrency_safe': False, 'interruptible': False},
}


def get_tool_concurrency(name):
    """返回 (concurrency_safe, interruptible)。未注册工具保守返回 (False, True)。"""
    meta = TOOL_CONCURRENCY.get(name)
    if meta:
        return bool(meta.get('concurrency_safe')), bool(meta.get('interruptible', True))
    return False, True


def _get_tool_timeout():
    """WP-5：单工具执行超时上界（秒）。env AGENT_TOOL_TIMEOUT 可配，默认 60。

    仅作用于 interruptible 工具；不可中断工具（写/生成类）保持无界——超时的
    后台线程无法安全回收，盲目截断可能破坏文件写入等操作的完整性。
    """
    try:
        return max(1, int(os.environ.get('AGENT_TOOL_TIMEOUT', '60')))
    except (TypeError, ValueError):
        return 60


def resolve_agent_tools(agent_config, allowed_tools):
    """WP-3：子代理工具裁剪 —— allowed_tools − agent.disallowed_tools。

    agent_config 可为 dict 或类 dict 行；disallowed_tools 支持 list 或 JSON
    字符串。返回去重后的白名单 list（保持原顺序）。任何解析异常时退化为
    原 allowed_tools（fail-open，与内核铁律一致）。
    """
    try:
        base = allowed_tools
        if isinstance(base, str):
            try:
                base = json.loads(base)
            except (json.JSONDecodeError, TypeError):
                base = None
        if not isinstance(base, list):
            base = []
        dis = None
        if isinstance(agent_config, dict):
            dis = agent_config.get('disallowed_tools')
        elif agent_config is not None:
            try:
                dis = agent_config['disallowed_tools']
            except (KeyError, IndexError, TypeError):
                dis = None
        if isinstance(dis, str):
            try:
                dis = json.loads(dis)
            except (json.JSONDecodeError, TypeError):
                dis = []
        if not isinstance(dis, list):
            dis = []
        dis_set = {str(x) for x in dis}
        seen, out = set(), []
        for name in base:
            n = str(name)
            if n in dis_set or n in seen:
                continue
            seen.add(n)
            out.append(name)
        return out
    except Exception as e:
        logger.warning('[Tools] resolve_agent_tools failed, passthrough: %s', e)
        base = allowed_tools
        if isinstance(base, str):
            try:
                return json.loads(base) or []
            except (json.JSONDecodeError, TypeError):
                return []
        return list(base) if isinstance(base, list) else []


# WP-5：planning 阶段禁止的写类内置工具（只读/查询类不在列）。
# MCP 工具的写判定由 plugin 声明演进，暂不在本门范围内（见 WP-4.5 备注）。
WRITE_TOOLS = frozenset({
    'ads_create', 'ads_update', 'ads_delete',
    'cms_create_post', 'cms_publish_post',
    'generate_ppt', 'generate_image', 'generate_markdown', 'generate_docx',
})


def get_parallel_enabled():
    """读取并发执行开关（system_config: agent_tool_parallel_enabled）。

    默认开启：并行前置条件已收敛为「开关开 + 批量>1 + 全部 concurrency_safe」
    （agent_runner._execute_batch），而 TOOL_CONCURRENCY 白名单里的
    concurrency_safe 工具全部是只读查询类（spawn_subagent 显式 False），
    因此默认开启不会引入任何写操作并发。

    DB 显式置 0/false 仍可关闭（回退位）。
    读取失败按**关闭**处理 —— 这里刻意不 fail-open 到并行：读取失败通常
    意味着 DB 本身有问题，此时叠加并发查询会加剧故障；串行路径与引入本
    开关前的行为逐字节一致，是更安全的一端。
    """
    try:
        from models import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key='agent_tool_parallel_enabled'"
            ).fetchone()
        if row and row['value'] is not None:
            return str(row['value']).strip().lower() in ('1', 'true', 'yes', 'on')
    except Exception as e:
        # 刻意不落到下面的缺省 True：DB 读不到时叠加并发会加剧故障。
        logger.warning('[Tools] parallel flag read failed, serial path: %s', e)
        return False
    return True


TOOL_EXECUTORS = {
    "get_system_health": _tool_get_system_health,
    "query_stats": _tool_query_stats,
    "search_knowledge": _tool_search_knowledge,
    "ads_list": _tool_ads_list,
    "ads_create": _tool_ads_create,
    "ads_update": _tool_ads_update,
    "ads_delete": _tool_ads_delete,
    "ads_get_stats": _tool_ads_get_stats,
    "ads_analyze": _tool_ads_analyze,
    "ads_render_snippet": _tool_ads_render_snippet,
    "cms_list_channels": _tool_cms_list_channels,
    "cms_create_post": _tool_cms_create_post,
    "cms_publish_post": _tool_cms_publish_post,
    "generate_ppt": _tool_generate_ppt,
    "generate_image": _tool_generate_image,
    "generate_markdown": _tool_generate_markdown,
    "generate_docx": _tool_generate_docx,
    "spawn_subagent": _tool_spawn_subagent,
}


# ============================================================
# 对外接口
# ============================================================

def get_tools_for_agent(allowed_tools):
    """按 Agent 的 allowed_tools 白名单返回可用工具 schema 列表。

    allowed_tools 可为 JSON 字符串或 list；为空/无效时返回空列表
    （即该 Agent 不启用工具，走原单轮逻辑）。
    """
    if not allowed_tools:
        return []
    if isinstance(allowed_tools, str):
        try:
            allowed_tools = json.loads(allowed_tools)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(allowed_tools, list):
        return []
    result = [TOOL_SCHEMAS[name] for name in allowed_tools if name in TOOL_SCHEMAS]
    # A4：MCP 工具合并改为受白名单约束（原实现无条件合并全部已启用插件的 MCP 工具，
    # 使 allowed_tools 语义失效）。仅当白名单显式含 mcp__ 条目或 'mcp__*' 时合并。
    if any(str(n).startswith('mcp__') for n in allowed_tools):
        try:
            from plugin_manager.mcp import get_enabled_mcp_tool_schemas
            schemas = get_enabled_mcp_tool_schemas()
            if 'mcp__*' not in allowed_tools:
                want = {n for n in allowed_tools if str(n).startswith('mcp__')}
                schemas = [s for s in schemas
                           if s['function']['name'] in want
                           or any(s['function']['name'].startswith(w + '__') for w in want)]
            result += schemas
        except Exception:
            pass
    return result


def _mcp_authorized(name, permitted):
    """MCP 工具按前缀授权：白名单中出现精确名、'mcp__<plugin_id>' 前缀、或 'mcp__*' 通配即放行。"""
    if 'mcp__*' in permitted or name in permitted:
        return True
    parts = name.split('__')
    # name 形如 mcp__<plugin_id>__<server>__<tool>，取 'mcp__<plugin_id>' 作为前缀比对
    if len(parts) >= 2:
        prefix = '__'.join(parts[:2])
        if prefix in permitted:
            return True
    return False


def _execute_mcp_tool(name, args, context=None):
    """P2-5: 路由 MCP 工具调用（mcp__<plugin>__<server>__<tool>）。"""
    try:
        from plugin_manager.mcp import call_mcp_tool
        return call_mcp_tool(name, args, context)
    except Exception as e:
        logger.warning(f'[tool:{name}] MCP 调用失败: {e}')
        return f'Tool {name} execution error: {e}'


def execute_tool(name, args, allowed_tools=None, context=None):
    """执行指定工具，返回字符串结果。

    A4：allowed_tools 非 None 时做执行层白名单强制（此前白名单只作用于 schema，
    模型被提示注入诱导即可调用未授权工具并真实执行）。

    WP-B（6.5）：在 A4 白名单之上叠加「执行前确认」门。过滤器
    `agent_tool.pre_execute` 可读取、改写本次调用信息或直接拒绝；无订阅者时
    apply_filters 原样返回初值，与引入本门之前逐字节一致。

    约束：审批门必须位于白名单分支**之外** —— agent_matrix/routes.py 调用本函数时
    不传 allowed_tools（即跳过白名单），若门写在分支内该路径将完全绕过审批。
    """
    # ── WP-B：执行前审批门（位置见 docstring 末段，勿移入白名单分支内）──
    gate = {
        'tool': name,
        'args': args if isinstance(args, dict) else {},
        'allowed': True,
        'agent_id': context.get('agent_id') if isinstance(context, dict) else None,
        'task_id': context.get('task_id') if isinstance(context, dict) else None,
        'agent_name': context.get('name') if isinstance(context, dict) else None,
    }
    try:
        from plugin_manager.hooks import get_hook_registry
        gate = get_hook_registry().apply_filters('agent_tool.pre_execute', gate)
    except Exception as e:
        logger.warning(f'[tool:{name}] approval gate failed, fail-open: {e}')
        gate = None
    # 类型兜底：回调返回 None / 字符串 / 其它非 dict 一律视为放行。
    # fail-open 铁律：回调写错不得导致全站工具不可用。
    if not isinstance(gate, dict):
        gate = {'allowed': True}
    if not gate.get('allowed', True):
        reason = gate.get('deny_reason') or 'Permission denied by approval gate'
        logger.warning(f'[tool:{name}] 被审批门拦截: {reason}')
        return reason

    # WP-5：planning 只读门 —— 任务处于 planning 阶段时拒绝全部写类内置工具。
    # phase 经 context 透传（agent_runner 执行链）；无 phase → 不拦截。
    if isinstance(context, dict) and context.get('task_phase') == 'planning':
        if name in WRITE_TOOLS:
            logger.warning(f'[tool:{name}] blocked by planning read-only gate')
            return ("Permission denied: tool " + name +
                    " is blocked during the planning phase (read-only gate)")

    if allowed_tools is not None:
        permitted = set(allowed_tools)
        if name.startswith('mcp__') and not _mcp_authorized(name, permitted):
            logger.warning(f'[tool:{name}] 拒绝执行：不在 allowed_tools 白名单内')
            return f"Permission denied: tool {name} is not in allowed_tools"
        if not name.startswith('mcp__') and name not in permitted:
            logger.warning(f'[tool:{name}] 拒绝执行：不在 allowed_tools 白名单内')
            return f"Permission denied: tool {name} is not in allowed_tools"
    if name.startswith('mcp__'):
        return _execute_mcp_tool(name, args, context)
    executor = TOOL_EXECUTORS.get(name)
    if not executor:
        return f"Unknown tool: {name}"
    if not isinstance(args, dict):
        args = {}
    try:
        # WP-5：单工具执行超时上界 —— 仅 interruptible 工具生效（防单个工具挂死
        # 整个 ReAct 循环）；未知工具保守按 interruptible 处理。线程无法安全回收，
        # 故 shutdown(wait=False) 放行后台线程，进程退出时由守护线程自动回收。
        _, interruptible = get_tool_concurrency(name)
        timeout = _get_tool_timeout() if interruptible else None
        if timeout:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tool-timeout')
            _fut = _pool.submit(executor, args)
            try:
                return _fut.result(timeout=timeout)
            except _FutTimeout:
                logger.warning(f'[tool:{name}] 执行超过 {timeout}s，按超时中断处理')
                return f"Tool {name} timed out after {timeout}s"
            finally:
                _pool.shutdown(wait=False)
        if name == 'spawn_subagent':
            # WP-8：spawn_subagent 需要父执行上下文（白名单交集/预算/深度），
            # 特判透传；其余工具维持 executor(args) 原契约。
            return executor(args, context=context)
        return executor(args)
    except Exception as e:
        logger.warning(f"[tool:{name}] 未捕获异常: {e}")
        return f"Tool {name} execution error: {e}"
