package knowledgebase

import "strings"

func ParseFrontmatter(content string) (Frontmatter, string, bool) {
	text := strings.ReplaceAll(content, "\r\n", "\n")
	text = strings.TrimPrefix(text, "\ufeff")
	text = strings.TrimLeft(text, " \t\n")
	if !strings.HasPrefix(text, "---\n") {
		return Frontmatter{}, text, false
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return Frontmatter{}, text, false
	}
	raw := text[4 : 4+end]
	after := text[4+end+len("\n---"):]
	after = strings.TrimLeft(after, "\n")
	fm := Frontmatter{Raw: raw}
	for _, line := range strings.Split(raw, "\n") {
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		value = strings.Trim(strings.TrimSpace(value), "\"")
		switch key {
		case "type":
			fm.Type = value
		case "title":
			fm.Title = value
		case "description":
			fm.Description = value
		case "resource":
			fm.Resource = value
		case "tags":
			fm.Tags = parseInlineList(value)
		case "depends_on", "dependsOn":
			fm.DependsOn = parseInlineList(value)
		case "see_also", "seeAlso":
			fm.SeeAlso = parseInlineList(value)
		case "status":
			fm.Status = value
		case "owner":
			fm.Owner = value
		case "timestamp":
			fm.Timestamp = value
		}
	}
	fm.Routing = parseRoutingMetadata(raw)
	return fm, after, true
}

func parseRoutingMetadata(raw string) RoutingMetadata {
	var routing RoutingMetadata
	lines := strings.Split(raw, "\n")
	inRouting := false
	section := ""
	inPairs := false
	pairIndex := -1
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		indent := leadingSpaces(line)
		trimmed := strings.TrimSpace(line)
		if indent == 0 {
			inRouting = strings.TrimSuffix(trimmed, ":") == "routing"
			section = ""
			inPairs = false
			pairIndex = -1
			continue
		}
		if !inRouting {
			continue
		}
		if indent == 2 {
			key, value, ok := strings.Cut(trimmed, ":")
			key = strings.TrimSpace(key)
			value = strings.TrimSpace(value)
			section = key
			inPairs = false
			pairIndex = -1
			if ok && value != "" {
				switch key {
				case "aliases":
					routing.Aliases.Values = parseInlineList(value)
				case "keywords":
					routing.Keywords.Values = parseInlineList(value)
				}
			}
			continue
		}
		if indent == 4 {
			key, value, ok := strings.Cut(trimmed, ":")
			if !ok {
				continue
			}
			key = strings.TrimSpace(key)
			value = strings.TrimSpace(value)
			switch section {
			case "aliases":
				switch key {
				case "zh":
					routing.Aliases.ZH = parseInlineList(value)
				case "en":
					routing.Aliases.EN = parseInlineList(value)
				case "pairs":
					inPairs = true
				}
			case "keywords":
				switch key {
				case "zh":
					routing.Keywords.ZH = parseInlineList(value)
				case "en":
					routing.Keywords.EN = parseInlineList(value)
				}
			}
			continue
		}
		if section == "aliases" && inPairs && indent >= 6 {
			trimmed = strings.TrimPrefix(trimmed, "- ")
			key, value, ok := strings.Cut(trimmed, ":")
			if !ok {
				continue
			}
			key = strings.TrimSpace(key)
			value = strings.Trim(strings.TrimSpace(value), "\"")
			if strings.HasPrefix(strings.TrimSpace(line), "- ") {
				routing.Aliases.Pairs = append(routing.Aliases.Pairs, RoutingAliasPair{})
				pairIndex = len(routing.Aliases.Pairs) - 1
			}
			if pairIndex < 0 {
				continue
			}
			switch key {
			case "zh":
				routing.Aliases.Pairs[pairIndex].ZH = value
			case "en":
				routing.Aliases.Pairs[pairIndex].EN = value
			}
		}
	}
	return routing
}

func leadingSpaces(line string) int {
	count := 0
	for _, r := range line {
		if r != ' ' {
			break
		}
		count++
	}
	return count
}

func parseInlineList(value string) []string {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "[")
	value = strings.TrimSuffix(value, "]")
	if strings.TrimSpace(value) == "" {
		return nil
	}
	parts := strings.Split(value, ",")
	items := make([]string, 0, len(parts))
	for _, part := range parts {
		item := strings.Trim(strings.TrimSpace(part), "\"")
		if item != "" {
			items = append(items, item)
		}
	}
	return items
}
