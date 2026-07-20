package gbrain

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"sync"
)

var ErrClientClosed = errors.New("GBrain MCP client is closed")

type mcpCallResult struct {
	result json.RawMessage
	err    error
}

type MCPClient struct {
	reader *bufio.Scanner
	writer io.Writer
	closer io.Closer

	writeMu sync.Mutex
	mu      sync.Mutex
	nextID  int64
	pending map[int64]chan mcpCallResult
	closed  bool
	done    chan struct{}
	onError func(error)
}

func NewMCPClient(reader io.Reader, writer io.Writer, onError func(error)) *MCPClient {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 64*1024), 8*1024*1024)
	client := &MCPClient{
		reader:  scanner,
		writer:  writer,
		pending: map[int64]chan mcpCallResult{},
		done:    make(chan struct{}),
		onError: onError,
	}
	if closer, ok := writer.(io.Closer); ok {
		client.closer = closer
	}
	go client.readLoop()
	return client
}

func (c *MCPClient) Initialize(ctx context.Context) ([]string, error) {
	var initialized initializeResult
	if err := c.Call(ctx, "initialize", initializeParams{
		ProtocolVersion: mcpProtocolVersion,
		Capabilities:    map[string]any{},
		ClientInfo:      clientInfo{Name: "nexus-agents", Version: "p2"},
	}, &initialized); err != nil {
		return nil, fmt.Errorf("initialize GBrain MCP: %w", err)
	}
	if initialized.ProtocolVersion == "" {
		return nil, fmt.Errorf("initialize GBrain MCP: server omitted protocolVersion")
	}
	if err := c.Notify("notifications/initialized", map[string]any{}); err != nil {
		return nil, fmt.Errorf("notify GBrain MCP initialized: %w", err)
	}
	return c.ListTools(ctx)
}

func (c *MCPClient) ListTools(ctx context.Context) ([]string, error) {
	var result toolsListResult
	if err := c.Call(ctx, "tools/list", map[string]any{}, &result); err != nil {
		return nil, err
	}
	tools := make([]string, 0, len(result.Tools))
	for _, tool := range result.Tools {
		if name := strings.TrimSpace(tool.Name); name != "" {
			tools = append(tools, name)
		}
	}
	return tools, nil
}

func (c *MCPClient) CallTool(ctx context.Context, name string, arguments map[string]any, target any) error {
	var result callToolResult
	if err := c.Call(ctx, "tools/call", map[string]any{
		"name": name, "arguments": arguments,
	}, &result); err != nil {
		return err
	}
	texts := make([]string, 0, len(result.Content))
	for _, content := range result.Content {
		if content.Type == "text" && strings.TrimSpace(content.Text) != "" {
			texts = append(texts, content.Text)
		}
	}
	text := strings.Join(texts, "\n")
	if result.IsError {
		if text == "" {
			text = "unknown tool error"
		}
		return fmt.Errorf("GBrain tool %s failed: %s", name, text)
	}
	if target == nil || strings.TrimSpace(text) == "" {
		return nil
	}
	if err := json.Unmarshal([]byte(text), target); err != nil {
		return fmt.Errorf("decode GBrain tool %s result: %w", name, err)
	}
	return nil
}

func (c *MCPClient) Call(ctx context.Context, method string, params any, target any) error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return ErrClientClosed
	}
	c.nextID++
	id := c.nextID
	response := make(chan mcpCallResult, 1)
	c.pending[id] = response
	c.mu.Unlock()

	request := rpcRequest{JSONRPC: "2.0", ID: id, Method: method, Params: params}
	if err := c.writeJSON(request); err != nil {
		c.removePending(id)
		return err
	}

	select {
	case <-ctx.Done():
		c.removePending(id)
		return ctx.Err()
	case <-c.done:
		c.removePending(id)
		return ErrClientClosed
	case result := <-response:
		if result.err != nil {
			return result.err
		}
		if target == nil || len(result.result) == 0 || string(result.result) == "null" {
			return nil
		}
		if err := json.Unmarshal(result.result, target); err != nil {
			return fmt.Errorf("decode %s result: %w", method, err)
		}
		return nil
	}
}

func (c *MCPClient) Notify(method string, params any) error {
	return c.writeJSON(struct {
		JSONRPC string `json:"jsonrpc"`
		Method  string `json:"method"`
		Params  any    `json:"params,omitempty"`
	}{JSONRPC: "2.0", Method: method, Params: params})
}

func (c *MCPClient) Close() error {
	c.failAll(ErrClientClosed)
	if c.closer != nil {
		return c.closer.Close()
	}
	return nil
}

func (c *MCPClient) Done() <-chan struct{} {
	return c.done
}

func (c *MCPClient) writeJSON(value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	c.mu.Lock()
	closed := c.closed
	c.mu.Unlock()
	if closed {
		return ErrClientClosed
	}
	if _, err := c.writer.Write(data); err != nil {
		c.failAll(err)
		return err
	}
	return nil
}

func (c *MCPClient) readLoop() {
	for c.reader.Scan() {
		line := strings.TrimSpace(c.reader.Text())
		if line == "" {
			continue
		}
		var response rpcResponse
		if err := json.Unmarshal([]byte(line), &response); err != nil {
			c.failAll(fmt.Errorf("decode GBrain MCP response: %w", err))
			return
		}
		if len(response.ID) == 0 {
			continue
		}
		id, err := parseResponseID(response.ID)
		if err != nil {
			c.failAll(err)
			return
		}
		c.mu.Lock()
		pending := c.pending[id]
		delete(c.pending, id)
		c.mu.Unlock()
		if pending == nil {
			continue
		}
		if response.Error != nil {
			pending <- mcpCallResult{err: fmt.Errorf("GBrain MCP error %d: %s", response.Error.Code, response.Error.Message)}
			continue
		}
		pending <- mcpCallResult{result: response.Result}
	}
	err := c.reader.Err()
	if err == nil {
		err = io.EOF
	}
	c.failAll(err)
}

func (c *MCPClient) removePending(id int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.pending, id)
}

func (c *MCPClient) failAll(err error) {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.closed = true
	pending := c.pending
	c.pending = map[int64]chan mcpCallResult{}
	close(c.done)
	c.mu.Unlock()
	for _, waiter := range pending {
		waiter <- mcpCallResult{err: err}
	}
	if c.onError != nil && !errors.Is(err, ErrClientClosed) {
		c.onError(err)
	}
}

func parseResponseID(raw json.RawMessage) (int64, error) {
	value := strings.TrimSpace(string(raw))
	if value == "" {
		return 0, fmt.Errorf("GBrain MCP response has an empty id")
	}
	if strings.HasPrefix(value, `"`) {
		var text string
		if err := json.Unmarshal(raw, &text); err != nil {
			return 0, err
		}
		value = text
	}
	id, err := strconv.ParseInt(value, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("GBrain MCP response id %q is not numeric", value)
	}
	return id, nil
}
