import os, time, json, pika

def main():
    while True:
        try:
            conn = pika.BlockingConnection(pika.ConnectionParameters(host=os.getenv('RABBIT_HOST','rabbitmq')))
            ch = conn.channel()
            ch.exchange_declare(exchange='repository.events', exchange_type='fanout', durable=True)
            q = ch.queue_declare(queue='repository.audit', durable=True)
            ch.queue_bind(queue='repository.audit', exchange='repository.events')
            def receive(channel, method, properties, body):
                print('EVENT:', json.loads(body), flush=True)
                channel.basic_ack(method.delivery_tag)
            ch.basic_consume(queue='repository.audit', on_message_callback=receive)
            print('Audit worker ready', flush=True)
            ch.start_consuming()
        except Exception as exc:
            print('Worker reconnecting:', exc, flush=True)
            time.sleep(3)

if __name__ == '__main__':
    main()
