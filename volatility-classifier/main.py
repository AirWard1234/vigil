from rich.console import Console
from rich.panel import Panel

from classifier.range_model import expected_range
from classifier.regime import classify as classify_regime
from classifier.scorer import score
from data.fetcher import fetch_market_snapshot
from data.options import fetch_gex_snapshot, fetch_options_snapshot
from data.sentiment import fetch_overnight_sentiment
from ui.dashboard import render

console = Console()


def run_pipeline() -> dict:
    market = fetch_market_snapshot()
    options = fetch_options_snapshot()
    gex = fetch_gex_snapshot()
    sentiment = fetch_overnight_sentiment()
    regime = classify_regime(market, gex)
    range_forecast = expected_range(market, gex, sentiment, regime)

    verdict = score(market, options, sentiment, regime, range_forecast)

    return {
        "market": market,
        "options": options,
        "gex": gex,
        "sentiment": sentiment,
        "regime": regime,
        "range": range_forecast,
        "verdict": verdict,
    }


def main():
    console.print(
        Panel.fit(
            "[bold green]Volatility Classifier[/bold green]\n"
            "[dim]Service started successfully[/dim]",
            border_style="green",
        )
    )
    result = run_pipeline()
    render(
        result["market"],
        result["options"],
        result["gex"],
        result["sentiment"],
        result["regime"],
        result["range"],
        result["verdict"],
    )


if __name__ == "__main__":
    main()
