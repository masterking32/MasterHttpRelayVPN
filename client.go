// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================

package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"masterhttprelayvpn/internal/client"
	"masterhttprelayvpn/internal/config"
	lg "masterhttprelayvpn/internal/logger"
)

func main() {
	logger := lg.New("MasterHttpRelayVPN Client", "INFO")

	cfg, err := config.Load("config.toml")
	if err != nil {
		logger.Fatalf("<red>load config: <cyan>%v</cyan></red>", err)
	}

	app := client.New(cfg, logger)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	if err := app.Run(ctx); err != nil {
		logger.Fatalf("<red>run client: <cyan>%v</cyan></red>", err)
	}
}
