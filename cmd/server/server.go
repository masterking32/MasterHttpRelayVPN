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

	"masterhttprelayvpn/internal/config"
	lg "masterhttprelayvpn/internal/logger"
	"masterhttprelayvpn/internal/server"
)

func main() {
	logger := lg.New("MasterHttpRelayVPN Server", "INFO")

	cfg, err := config.Load("server.toml")
	if err != nil {
		logger.Fatalf("<red>load config: <cyan>%v</cyan></red>", err)
	}
	if err := cfg.ValidateServer(); err != nil {
		logger.Fatalf("<red>validate server config: <cyan>%v</cyan></red>", err)
	}

	logger = lg.New("MasterHttpRelayVPN Server", cfg.LogLevel)

	app := server.New(cfg, logger)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	if err := app.Run(ctx); err != nil {
		logger.Fatalf("<red>run server: <cyan>%v</cyan></red>", err)
	}
}
