"""Maidenhead Mapper QGIS plugin entry point.

Kept import-free so `maidenhead_mapper.core` can be tested without QGIS.
"""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import MaidenheadMapperPlugin

    return MaidenheadMapperPlugin(iface)
