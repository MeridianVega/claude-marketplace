# Meridian Vega Claude Code Marketplace

Plugins published by [Meridian Vega](https://meridianvega.com) for use with [Claude Code](https://code.claude.com).

## Install

Run inside any Claude Code session:

```text
/plugin marketplace add MeridianVega/claude-marketplace
/plugin install ersatztv-programmer@meridianvega
```

Use `/plugin marketplace update` to pull the latest catalog.

## Plugins

| Plugin | Purpose |
| --- | --- |
| [`ersatztv-programmer`](./plugins/ersatztv-programmer) | Schedule ErsatzTV Next channels from a Jellyfin or Plex library. Plan, Smart Shuffle, Live, Calendar-Based, and Seasonal channel modes. Emits playout JSON consumed by ErsatzTV Next's streaming engine. |

## Contributing a plugin

1. Create `plugins/<plugin-name>/` containing `.claude-plugin/plugin.json`.
2. Add a corresponding entry to `.claude-plugin/marketplace.json`.
3. Bump `metadata.version`.
4. Open a pull request.

Plugin manifest reference: [Claude Code plugin docs](https://code.claude.com/docs/en/plugins).

## Support

[support@meridianvega.com](mailto:support@meridianvega.com)

## License

[MIT](./LICENSE)
