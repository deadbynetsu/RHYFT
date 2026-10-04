# -*- coding: utf-8 -*-
"""Gera uma chave de assinatura Android e os valores para GitHub Actions."""
import base64
import datetime
import os
import secrets
import string
import sys

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.exit('Falta a biblioteca "cryptography". Instale com: pip install cryptography')

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'chave-android')
ARQUIVO_JKS = 'migrador-chave.jks'
ARQUIVO_SEGREDOS = 'segredos-para-o-github.txt'
ALIAS = 'migrador'


def gerar_senha(tamanho=32):
    letras = string.ascii_letters + string.digits
    return ''.join(secrets.choice(letras) for _ in range(tamanho))


def main():
    pasta = os.path.abspath(PASTA)
    caminho_jks = os.path.join(pasta, ARQUIVO_JKS)
    if os.path.exists(caminho_jks):
        sys.exit(f'Já existe uma chave em {caminho_jks}.\nNÃO gere outra: trocar a chave impede atualizar o app por cima.')

    senha = gerar_senha()
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'Migrador de Playlists'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'deadbynetsu'),
    ])
    agora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - datetime.timedelta(minutes=5))
            .not_valid_after(agora + datetime.timedelta(days=36500))
            .sign(chave, hashes.SHA256()))
    dados = pkcs12.serialize_key_and_certificates(
        ALIAS.encode('utf-8'), chave, cert, None,
        serialization.BestAvailableEncryption(senha.encode('utf-8')))

    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, '.gitignore'), 'w', encoding='utf-8') as f:
        f.write('# NUNCA envie a chave para o GitHub\n*\n')
    with open(caminho_jks, 'wb') as f:
        f.write(dados)
    b64 = base64.b64encode(dados).decode('ascii')
    with open(os.path.join(pasta, ARQUIVO_SEGREDOS), 'w', encoding='utf-8') as f:
        f.write(f'''ANDROID_KEYSTORE_PASSWORD\n{senha}\n\nANDROID_KEY_PASSWORD\n{senha}\n\nANDROID_KEY_ALIAS\n{ALIAS}\n\nANDROID_KEYSTORE_BASE64\n{b64}\n''')

    print(f'Pronto: {pasta}')
    print('Faça backup do .jks, cadastre os 4 segredos no GitHub Actions e apague o arquivo de segredos.')


if __name__ == '__main__':
    main()
