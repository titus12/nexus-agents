package knowledgebase

import (
	"encoding/json"
	"fmt"
	"testing"
)

func TestZZBTDMojibakeValidation(t *testing.T) {
	report, err := Validate("D:/workspace/src/btd-client")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.MarshalIndent(report, "", "  ")
	fmt.Println(string(b))
}
