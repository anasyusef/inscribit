FROM python:3.10

WORKDIR /code

# Get Rust
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y

ENV PATH="/root/.cargo/bin:${PATH}"

RUN git clone https://github.com/casey/ord.git && cd ord && cargo build --release

RUN mv ord/target/release/ord /usr/bin/

COPY ./log.ini /code/log.ini

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# Update default packages
RUN apt-get update

# Get Ubuntu packages
RUN apt-get install -y \
    build-essential \
    curl \
    libssl-dev

COPY ./app /code/app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80", "--log-config", "log.ini"]