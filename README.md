# 📦 Encomendas do Condomínio

Sistema web para a portaria: lê o QR code / código de barras da etiqueta,
identifica o morador e avisa pelo WhatsApp que a encomenda chegou.
Na retirada, o porteiro confere um código de 4 dígitos enviado ao morador.

## Como funciona

1. Porteiro toca em **Ler QR code** (câmera do celular/tablet).
2. Se o morador **pré-cadastrou o rastreio**, o sistema já sabe de quem é.
   Senão, o porteiro digita o apto (ex.: `302`, `A302`) ou o nome e escolhe.
3. **Registrar e avisar** → salva a encomenda e manda a mensagem:
   > Olá João! Chegou uma encomenda para Bloco A - Apto 302 na portaria do Residencial Exemplo. Código de retirada: 2614
4. Na retirada: **Entregar** → digita o código informado pelo morador.
   (Se o morador perdeu o código, marca a opção e registra quem retirou.)

Se o envio falhar, a encomenda **fica registrada** e aparece com ⚠ e um botão 🔁 para reenviar.

## Rodar no computador

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # edite nome do condomínio e senha
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra <http://localhost:8000> e entre com usuário/senha do `.env`
(padrão `portaria` / `trocar123` — **troque**).

Com `NOTIFICADOR=console` as mensagens só aparecem no terminal — ótimo para testar.

### Cadastrar moradores

Na tela **Moradores**: um a um, ou importando uma planilha CSV com as colunas
`nome, telefone, bloco, apto` (veja `moradores_exemplo.csv`). No Excel: *Salvar como → CSV*.

## Usar no celular da portaria

O navegador só libera a câmera em **HTTPS**. Opções:

- **Teste rápido:** com o servidor rodando, `cloudflared tunnel --url http://localhost:8000`
  gera um endereço `https://...trycloudflare.com` para abrir no celular.
- **Definitivo:** hospedar em Railway, Render, Fly.io ou uma VPS com domínio + HTTPS.
  Guarde o arquivo `encomendas.db` em disco persistente (é o banco de dados).

Dica: no celular, use “Adicionar à tela inicial” para abrir como um app.

## Ativar o WhatsApp (API oficial da Meta)

1. Crie um app em <https://developers.facebook.com> → produto **WhatsApp**.
2. Cadastre um número de telefone **exclusivo** para o condomínio (não pode estar em uso no WhatsApp comum).
3. Em *Modelos de mensagem*, crie um modelo categoria **Utilidade**, idioma **Português (BR)**, nome `encomenda_chegou`:
   ```
   Olá {{1}}! Chegou uma encomenda para {{2}} na portaria. Código de retirada: {{3}}
   ```
4. Gere um **token permanente** (usuário do sistema no Gerenciador de Negócios) e copie o **Phone number ID**.
5. No `.env`:
   ```
   NOTIFICADOR=whatsapp
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_NUMBER_ID=...
   ```

Custo: a Meta cobra por mensagem de utilidade (alguns centavos cada). Confira a tabela atual de preços para o Brasil.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

## LGPD

O sistema guarda nome, telefone e unidade dos moradores. Peça o consentimento para envio
de avisos, use uma senha forte na portaria e restrinja o acesso ao arquivo do banco.

## Estrutura

```
app/
  main.py          API (FastAPI) e rotas das telas
  db.py            tabelas SQLite
  notificador.py   envio do aviso (console / WhatsApp)
  config.py        configurações (.env)
  static/          telas da portaria e de moradores
tests/             testes automatizados
```

## Próximos passos sugeridos

- Foto da encomenda no registro
- Lembrete automático para encomendas paradas há X dias
- Portal do morador para pré-cadastrar rastreios sozinho
- Leitura (OCR) do nome/apto impresso na etiqueta
- Logins individuais por porteiro
