"""agent_matrix.deck — ResearchDeck 文档契约（T0a）。

只描述「文档怎么被描述」：通用外壳 + sections[] + ext 业务扩展位。
放内核而非插件的理由：它与金融无关，非金融 edition 要复用同一套渲染器。
渲染器读外壳，ext 一律不读。
"""

from .schema import (  # noqa: F401
    SCHEMA_VERSION,
    SECTION_TYPES,
    ENVELOPE_FIELDS,
    validate_deck,
)

__all__ = ["SCHEMA_VERSION", "SECTION_TYPES", "ENVELOPE_FIELDS", "validate_deck"]
