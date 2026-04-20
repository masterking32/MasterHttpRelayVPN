// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"masterhttprelayvpn/internal/protocol"
)

type sendWorker struct {
	id         int
	httpClient *http.Client
}

type dequeuedPacket struct {
	socksConn *SOCKSConnection
	item      *SOCKSOutboundQueueItem
}

func (c *Client) startSendWorkers(ctx context.Context, wg *sync.WaitGroup) {
	for i := 0; i < c.cfg.WorkerCount; i++ {
		worker := &sendWorker{
			id: i + 1,
			httpClient: &http.Client{
				Timeout: time.Duration(c.cfg.HTTPRequestTimeoutMS) * time.Millisecond,
			},
		}

		wg.Add(1)
		go func(w *sendWorker) {
			defer wg.Done()
			w.run(ctx, c)
		}(worker)
	}
}

func (w *sendWorker) run(ctx context.Context, c *Client) {
	pollInterval := time.Duration(c.cfg.WorkerPollIntervalMS) * time.Millisecond

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		batch, selected := c.buildNextBatch()
		if len(selected) == 0 {
			time.Sleep(pollInterval)
			continue
		}

		if err := batch.Validate(); err != nil {
			c.log.Errorf("<red>worker=<cyan>%d</cyan> invalid batch: <cyan>%v</cyan></red>", w.id, err)
			c.requeueSelected(selected)
			time.Sleep(pollInterval)
			continue
		}

		body, err := encryptBatch(batch, c.cfg.AESEncryptionKey)
		if err != nil {
			c.log.Errorf("<red>worker=<cyan>%d</cyan> encrypt batch failed: <cyan>%v</cyan></red>", w.id, err)
			c.requeueSelected(selected)
			time.Sleep(pollInterval)
			continue
		}

		if err := w.postBatch(ctx, c, batch, body); err != nil {
			c.log.Warnf("<yellow>worker=<cyan>%d</cyan> send failed for batch=<cyan>%s</cyan>: <cyan>%v</cyan></yellow>", w.id, batch.BatchID, err)
			c.requeueSelected(selected)
			time.Sleep(pollInterval)
			continue
		}

		c.log.Debugf(
			"<green>worker=<cyan>%d</cyan> sent batch=<cyan>%s</cyan> packets=<cyan>%d</cyan> bytes=<cyan>%d</cyan></green>",
			w.id, batch.BatchID, len(batch.Packets), len(body),
		)
	}
}

func (c *Client) buildNextBatch() (protocol.Batch, []dequeuedPacket) {
	connections := c.socksConnections.Snapshot()
	selected := make([]dequeuedPacket, 0, c.cfg.MaxPacketsPerBatch)
	packets := make([]protocol.Packet, 0, c.cfg.MaxPacketsPerBatch)
	totalBytes := 0

	for len(selected) < c.cfg.MaxPacketsPerBatch {
		progress := false

		for _, socksConn := range connections {
			if len(selected) >= c.cfg.MaxPacketsPerBatch {
				break
			}

			item := socksConn.DequeuePacket()
			if item == nil {
				continue
			}

			packetBytes := len(item.Packet.Payload)
			if len(selected) > 0 && totalBytes+packetBytes > c.cfg.MaxBatchBytes {
				socksConn.RequeueFront([]*SOCKSOutboundQueueItem{item})
				continue
			}

			selected = append(selected, dequeuedPacket{
				socksConn: socksConn,
				item:      item,
			})
			packets = append(packets, item.Packet)
			totalBytes += packetBytes
			progress = true
		}

		if !progress {
			break
		}
	}

	if len(packets) == 0 {
		return protocol.Batch{}, nil
	}

	batch := protocol.NewBatch(c.clientSessionKey, protocol.NewBatchID(), packets)
	return batch, selected
}

func (c *Client) requeueSelected(selected []dequeuedPacket) {
	grouped := make(map[*SOCKSConnection][]*SOCKSOutboundQueueItem)
	for _, entry := range selected {
		grouped[entry.socksConn] = append(grouped[entry.socksConn], entry.item)
	}

	for socksConn, items := range grouped {
		socksConn.RequeueFront(items)
	}
}

func (w *sendWorker) postBatch(ctx context.Context, c *Client, batch protocol.Batch, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.RelayURL, bytes.NewReader(body))
	if err != nil {
		return err
	}

	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("X-Relay-Version", fmt.Sprintf("%d", protocol.CurrentVersion))

	resp, err := w.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(respBody))
	}

	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

func encryptBatch(batch protocol.Batch, keyText string) ([]byte, error) {
	plain, err := json.Marshal(batch)
	if err != nil {
		return nil, err
	}

	key := sha256.Sum256([]byte(keyText))
	block, err := aes.NewCipher(key[:])
	if err != nil {
		return nil, err
	}

	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}

	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}

	ciphertext := gcm.Seal(nil, nonce, plain, nil)
	return append(nonce, ciphertext...), nil
}
