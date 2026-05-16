FROM python:3.11-slim-bookworm

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib
RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib/ \
    && ./configure --prefix=/usr \
    && make \
    && make install \
    && cd .. \
    && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# Create non-root user
RUN useradd --create-home --shell /bin/bash xau_trader
WORKDIR /home/xau_trader/app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY --chown=xau_trader:xau_trader . .

# Switch to non-root user
USER xau_trader

# Create log dir
RUN mkdir -p logs

# Run the trading system
CMD ["python", "-m", "src.main"]
