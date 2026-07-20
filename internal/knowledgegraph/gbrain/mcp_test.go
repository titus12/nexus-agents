package gbrain

import (
	"bufio"
	"context"
	"encoding/json"
	"net"
	"testing"
	"time"
)

func TestMCPClientInitializesAndListsTools(t *testing.T) {
	clientSide, serverSide := net.Pipe()
	defer serverSide.Close()
	client := NewMCPClient(clientSide, clientSide, nil)
	defer client.Close()

	serverDone := make(chan error, 1)
	go func() {
		reader := bufio.NewScanner(serverSide)
		for reader.Scan() {
			var request map[string]any
			if err := json.Unmarshal(reader.Bytes(), &request); err != nil {
				serverDone <- err
				return
			}
			method, _ := request["method"].(string)
			id, hasID := request["id"]
			if !hasID {
				if method == "notifications/initialized" {
					continue
				}
				continue
			}
			var result any
			switch method {
			case "initialize":
				result = map[string]any{
					"protocolVersion": mcpProtocolVersion,
					"capabilities":    map[string]any{"tools": map[string]any{}},
					"serverInfo":      map[string]any{"name": "gbrain", "version": "test"},
				}
			case "tools/list":
				result = map[string]any{"tools": []map[string]any{{"name": "search"}, {"name": "doctor"}}}
			default:
				continue
			}
			response, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
			response = append(response, '\n')
			if _, err := serverSide.Write(response); err != nil {
				serverDone <- err
				return
			}
			if method == "tools/list" {
				serverDone <- nil
				return
			}
		}
		serverDone <- reader.Err()
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	tools, err := client.Initialize(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(tools) != 2 || tools[0] != "search" || tools[1] != "doctor" {
		t.Fatalf("tools = %#v", tools)
	}
	if err := <-serverDone; err != nil {
		t.Fatal(err)
	}
}

func TestMCPClientHonorsRequestTimeout(t *testing.T) {
	clientSide, serverSide := net.Pipe()
	defer serverSide.Close()
	client := NewMCPClient(clientSide, clientSide, nil)
	defer client.Close()
	go func() {
		reader := bufio.NewScanner(serverSide)
		reader.Scan()
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	if err := client.Call(ctx, "tools/list", map[string]any{}, nil); err != context.DeadlineExceeded {
		t.Fatalf("error = %v", err)
	}
}

func TestMCPClientCallsToolAndDecodesTextJSON(t *testing.T) {
	clientSide, serverSide := net.Pipe()
	defer serverSide.Close()
	client := NewMCPClient(clientSide, clientSide, nil)
	defer client.Close()
	go func() {
		reader := bufio.NewScanner(serverSide)
		if !reader.Scan() {
			return
		}
		var request map[string]any
		_ = json.Unmarshal(reader.Bytes(), &request)
		response, _ := json.Marshal(map[string]any{
			"jsonrpc": "2.0",
			"id":      request["id"],
			"result": map[string]any{
				"content": []map[string]any{{"type": "text", "text": `{"ok":true,"slug":"project"}`}},
			},
		})
		_, _ = serverSide.Write(append(response, '\n'))
	}()
	var result struct {
		OK   bool   `json:"ok"`
		Slug string `json:"slug"`
	}
	if err := client.CallTool(context.Background(), "put_page", map[string]any{"slug": "project"}, &result); err != nil {
		t.Fatal(err)
	}
	if !result.OK || result.Slug != "project" {
		t.Fatalf("result = %#v", result)
	}
}
