# Automação do Instagram — Vértices Tecnologias

Robô que publica sozinho, no @vertices_tecnologias, **um post e um story** em 4 dias por semana, nos melhores horários:

| Dia | Post | Story |
|---|---|---|
| Terça | 19h | 21h |
| Quarta | 12h | 18h |
| Quinta | 9h | 12h |
| Domingo | 21h | 22h |

Os horários ficam em `config.json` e podem ser mudados a qualquer momento.

A fila atual tem **17 posts inéditos, de 24/11 a 22/12/2026**, todos conferidos contra o feed em 23/11/2026, para não repetir nada que já saiu.

---

## Como funciona

```
fila/
  2026-11-24_mascaras-sob-medida/
    01.jpg 02.jpg ...      fotos do post (1 foto = post simples, 2 a 10 = carrossel)
    caption.txt            legenda do post
    story.jpg              arte do story
    story_caption.txt      frase escrita no story
postados/                  pastas que já saíram (o robô move sozinho)
estado.json               registro do que foi publicado
```

De 30 em 30 minutos o GitHub roda o robô. Ele olha a pasta do dia e:
1. na hora do post, publica o post;
2. na hora do story (e pelo menos 1h depois do post), publica o story;
3. move a pasta para `postados/`.

Se o robô ficar parado alguns dias, ele publica os atrasados **um de cada vez, com 3h de intervalo**, nunca tudo de uma vez.

### Avisos automáticos (chegam no seu e-mail do GitHub)
- **Fila acabando:** quando faltar menos de 10 dias de conteúdo.
- **Token parou de funcionar:** se o Instagram recusar o acesso.
- **Falha numa publicação:** o GitHub manda e-mail sempre que uma rodada dá erro.

### Por que a automação antiga parou
Ela postou de 02/09 a 31/10/2026, exatamente 60 dias: é a validade do token comum da Meta. O token venceu e nada avisou. Agora:
- o passo 3 usa um token de **usuário do sistema, que não expira**;
- se o token falhar, o robô abre um aviso que chega por e-mail;
- o GitHub desliga agendamentos de repositórios parados há 60 dias, mas aqui o robô salva o progresso a cada publicação, então isso não acontece.

---

## Ativação (uma vez só)

### 1. Criar o repositório
Na conta **verticestecnologias-byte** do GitHub: **New repository** → nome `automacao-instagram` → **Public** → *Create repository*.

> Precisa ser **público**: o Instagram baixa as fotos por um link público do GitHub. Nenhuma senha ou token fica nos arquivos; eles vão nos *Secrets* (passo 4), que ninguém vê.

### 2. Enviar estes arquivos
Duas opções:
- **Mais fácil:** libere o repositório para o app do Claude (https://github.com/settings/installations → Claude → *Configure* → marque `automacao-instagram`) e peça ao Claude para subir os arquivos.
- **Manual:** com o GitHub Desktop, *File → Add local repository* → escolha a pasta `Automacao Instagram` → *Publish repository* (desmarque "Keep this code private").

### 3. Gerar o token da Meta (que não expira)
A conta do Instagram precisa ser **profissional** (Empresa ou Criador) e estar **ligada à Página do Facebook** da Vértices.

1. Em https://developers.facebook.com → **Meus apps** → crie (ou use o da automação antiga) um app do tipo **Empresa**, e adicione o produto **Instagram** (API com login do Facebook).
2. Em https://business.facebook.com/settings → **Usuários → Usuários do sistema** → **Adicionar** (função *Admin*).
3. Nesse usuário, **Atribuir ativos**: a Página do Facebook e a conta do Instagram da Vértices (controle total).
4. Clique em **Gerar token** → escolha o app → validade **Nunca** → marque as permissões:
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`.
5. Copie o token (ele só aparece uma vez).
6. Para descobrir o **ID da conta do Instagram**: em https://developers.facebook.com/tools/explorer, cole o token, faça a consulta
   `me/accounts?fields=name,instagram_business_account` e copie o número de `instagram_business_account → id`.

### 4. Guardar o token no GitHub
No repositório: **Settings → Secrets and variables → Actions → New repository secret**:
- `IG_USER_ID` → o número do passo 3.6
- `IG_ACCESS_TOKEN` → o token do passo 3.5

### 5. Testar
Aba **Actions** → se pedir, clique em *I understand my workflows, go ahead and enable them*.
1. **Checagem semanal (token e fila)** → *Run workflow*. Deve aparecer `token OK — conta @vertices_tecnologias`.
2. **Publicar no Instagram** → *Run workflow* → em "Só simular" deixe `true`. Mostra o que seria publicado, sem postar nada.

Pronto. A partir daí ele roda sozinho.

---

## Colocar posts novos na fila

1. Crie uma pasta em `fila/` com o nome `AAAA-MM-DD_nome-do-produto` (a data é o dia da publicação; use terça, quarta, quinta ou domingo).
2. Coloque dentro:
   - as fotos (qualquer nome, `.png` ou `.jpg`; a ordem alfabética vira a ordem do carrossel);
   - `caption.txt` com a legenda (sem preço, chamando no direct);
   - `story_caption.txt` com a frase curta do story (ex.: `Olha essa fofura se mexendo! Post completo no feed!`).
3. Envie para o GitHub. Na próxima rodada o robô converte as fotos e **cria o story sozinho** a partir da primeira foto.

Se quiser outra foto no story, coloque um arquivo chamado `story_base.jpg` (ou `.png`) na pasta.

## Comandos úteis (no computador, com Python)
```
pip install -r requirements.txt
python preparar.py              # prepara as fotos e os stories da fila
python publicar.py --agenda     # mostra a agenda
```
