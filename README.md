# ⚡️ FLOP Devnet GPU Miner Kurulumu

Vast.ai'dan NVIDIA GPU'lu bir VM kiralayıp FLOP miner kuruyoruz. Modeli çalıştıran servis `inference`, ağla konuşan servis `miner`. Adımları sırayla takip edin. 👇

**4 Kasım 2026 için hazırlandı.** Devnet ödülsüz; GPU kiralama ise ücretli. İlk denemeyi tek makineyle yapın.

📌 Canlı GPU kurulumu henüz test edilmedi. Komut ve ağ kontrolleri, gerçek miner kabul testinin yerine geçmez.

## 1. Mac'te SSH anahtarı oluşturalım

Bu anahtar sunucuya bağlanmak için. **Mac'in Terminal uygulamasında** çalıştırın. Mevcut `flop_devnet` anahtarı varsa yeniden oluşturmaz:

```sh
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [ -e "$HOME/.ssh/flop_devnet" ] || [ -e "$HOME/.ssh/flop_devnet.pub" ]; then
  echo 'Bu isimde anahtar var; mevcut dosyalar korundu.'
else
  ssh-keygen -t ed25519 -f "$HOME/.ssh/flop_devnet" -C 'flop-devnet'
fi
cat "$HOME/.ssh/flop_devnet.pub"
```

`ssh-ed25519` ile başlayan satırı kopyalayın. Vast'a bu **public** anahtarı vereceğiz. `.pub` olmadan biten dosya sizde kalır.

## 2. Vast'a anahtarı ekleyip VM kiralayalım

[Vast.ai](https://cloud.vast.ai/?ref_id=846816) hesabında **Keys → SSH Keys** bölümüne public anahtarı ekleyin. Bunu **kiralamadan önce** yapın. **Billing** bölümünden deneme bütçeniz kadar kredi yükleyin.

**Search → Change Template → VM → Ubuntu 22.04 VM** seçin. Normal Docker şablonunu seçmeyin; içeride Docker çalıştıracağımız için tam VM gerekiyor.

| Kontrol | Seçim |
| --- | --- |
| Kiralama | İlk deneme için On-Demand, tek GPU |
| GPU | NVIDIA; ilk deneme için 16 GB veya üzeri VRAM |
| Max CUDA | Bu rehberdeki imajlar için `13.0` veya üzeri |
| Sürücü | NVIDIA `580` ailesi veya üzeri |
| Disk | En az `60 GB`; daha fazla model için pay bırakın |
| Ücret | GPU + disk + internet toplamına bakın |

**RENT** sonrası **Instances** ekranında VM'nin açılmasını bekleyin. Sonuç: SSH bağlantısı verilebilen çalışan bir VM. Kartın modelle uyumu ve kalibrasyon sonucu ayrıca kontrol edilecek.

## 3. Sunucuya bağlanalım

Vast'ta **Connect → Direct SSH** bilgisini alın. `PORT` ve `SUNUCU_IP` alanlarını onunla değiştirin. **Mac terminalinde:**

```sh
ssh -i "$HOME/.ssh/flop_devnet" -p PORT root@SUNUCU_IP
```

Bağlanınca `root@…` benzeri bir sunucu satırı görürsünüz. **Bundan sonraki komutlar Mac'te değil, bu sunucuda çalışır.**

## 4. Rehber dosyalarını indirelim

Gerekli küçük araçları kurup repoyu alıyoruz:

```sh
apt-get update
apt-get install -y git curl jq python3 openssl nano ca-certificates gnupg
cd /root
git clone https://github.com/0xkuzeydurden/flop-devnet-rehberi.git
cd flop-devnet-rehberi
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
docker --version
docker compose version
```

GPU adı, sürücü ve Docker/Compose sürümleri görünmeli. Docker yoksa seçtiğiniz şablonu kontrol edin; bu akış Ubuntu VM içindir.

## 5. GPU'yu Docker'a bağlayalım

NVIDIA Container Toolkit, konteynerin ekran kartını kullanmasını sağlar:

```sh
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
docker run --rm --gpus all nvidia/cuda:13.0.1-base-ubuntu22.04 nvidia-smi
bash scripts/check-host.sh
```

GPU tablosu Docker içinden de görünmeli. Son kontrolde eksik gereksinim hatası varsa burada çözün.

## 6. FLOP ayarlarını oluşturalım

Bu komut resmî manifestten imajları, kayıtlı Qwen modelini ve model ayarlarını alır. Yeni `.env` dosyasını oluşturur:

```sh
python3 scripts/prepare-env.py --write
python3 scripts/network-check.py
```

`Created private .env` dosyanın oluştuğunu gösterir. Ağ kontrolünde `matches_reviewed_profile: true` ve **“Ağ kontrolü tamam”** bekliyoruz. Ağ sıfırlanmış veya kimlik uyuşmamışsa araç durur; devam etmeyin. Aynı `.env` varsa üzerine yazılmaz.

Model `Qwen/Qwen2.5-0.5B-Instruct`, BF16. İmajlar sürüm ve SHA-256 ile sabitlenir; `latest` seçmiyoruz.

## 7. Miner hesabını oluşturalım

Önce FLOP komutunu hazırlıyoruz. Bu kısayol mevcut SSH oturumunda geçerli:

```sh
FLOP_CLI="$(sed -n 's/^FLOP_CLI_IMAGE=//p' .env)"
: "${FLOP_CLI:?Önce .env dosyasını oluşturun}"
flop() {
  docker run --rm --network host -v "$PWD:/work" "$FLOP_CLI" "$@"
}
docker pull "$FLOP_CLI"
mkdir -p private/checkpoint
chmod 700 private
```

Şimdi yeni hesap ve session anahtarı. Secret'lar ekrana yazılmaz; yalnız public adres gösterilir:

```sh
(
  set -eu
  umask 077
  test ! -e private/miner-key.json
  test ! -e private/miner.seed
  test ! -e private/session.seed
  flop keys generate --type sr25519 --show-secret --json > private/miner-key.json
  jq -er '.mnemonic' private/miner-key.json > private/miner.seed
  openssl rand -hex 32 > private/session.seed
  jq -er '.address' private/miner-key.json
)
```

`5…` biçiminde miner adresinizi görmelisiniz. Yeniden kurulumda bu adımı mevcut anahtarların üzerine uygulamayın.

## 8. Sponsorla kayıt olalım

Sponsor, devnet kaydı için stake sağlar. Önce hiçbir istek göndermeyen prova:

```sh
docker run --rm --entrypoint node \
  -v "$PWD/scripts:/work/scripts:ro" \
  "$FLOP_CLI" /work/scripts/sponsor.mjs
```

Gerçek kaydı göndermek için:

```sh
docker run --rm --entrypoint node \
  -v "$PWD/scripts:/work/scripts:ro" \
  -v "$PWD/private/miner.seed:/run/flop/miner.seed:ro" \
  "$FLOP_CLI" /work/scripts/sponsor.mjs \
  --submit --key-file /run/flop/miner.seed
```

Ağ kontrolü geçince `sponsorReportedSuccess: true` bekliyoruz. Ardından explorer'da hesabınızı kontrol edin. Başarılıysa ayrıca `miner register` çalıştırmayın; stake harcanabilir kazancınız değil.

- `403`: Hesap kabulü/yetkilendirme koşulu olabilir. Yanıtı okuyup burada durun.
- `429`: Limit veya kontenjan dolu olabilir.
- `503` veya bağlantı kesilmesi: Yeniden göndermeden önce explorer'da kayıt ve stake'i kontrol edin. İstek ulaşmış olabilir; peş peşe kayıt isteği göndermeyin.

## 9. Kayıtlı modeli indirelim

CLI modeli indirip dosyaları kayıtlı hash ile karşılaştırır:

```sh
MODEL_HASH="$(sed -n 's/^MODEL_HASH=//p' .env)"
flop --network devnet --rpc wss://rpc.devnet.flop.network \
  model fetch "$MODEL_HASH" --precision BF16 \
  --output-dir /work/private/checkpoint --json
```

Başarılı indirme ve model doğrulama sonucu görünmeli. Hash uyuşmazlığını başka checkpoint koyarak geçmeyin.

## 10. HTTPS tünelini açalım

Miner'a dışarıdan ulaşılması için geçici HTTPS adresi oluşturuyoruz. **Bu ilk SSH terminali açık kalacak:**

```sh
curl -fL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
  -o /tmp/flop-cloudflared.deb
dpkg -i /tmp/flop-cloudflared.deb
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8645
```

Çıktıda `https://….trycloudflare.com` adresini bulun. Tünel kapanırsa adres çalışmaz; yeniden açıldığında değişebilir. Kalıcı kullanım için sabit domain/tünel kullanın.

**Mac'te ikinci bir terminal açın**, 3. adımdaki SSH komutuyla tekrar bağlanın. Sunucuda:

```sh
cd /root/flop-devnet-rehberi
nano .env
```

Yalnız bu iki alanı düzenleyin:

```dotenv
WORKER_NAME=minerinizin-ismi
SESSION_PUBLIC_HOST=size-verilen-adres.trycloudflare.com
```

Hostname'e `https://` eklemeyin. `Ctrl+O`, Enter, `Ctrl+X` ile kaydedip çıkın. Diğer değerleri koruyun.

## 11. Modeli ve miner'ı başlatalım

**İkinci SSH terminalinde** önce modeli açıp sağlık kontrolünü yapıyoruz:

```sh
docker compose config --quiet
docker compose pull
docker compose up -d inference
docker compose logs --tail=80 inference
bash scripts/miner-health.sh
```

İlk yükleme biraz sürebilir. Sağlık kontrolünde dört `OK` satırı ve **“Model kontrolü tamam”** bekliyoruz. Henüz cevap vermiyorsa loga bakıp hazır olmasını bekleyin.

Kontrol geçince miner'ı açın:

```sh
docker compose up -d miner
docker compose logs -f miner
```

Kendi public adresinizi, model kontrolünü ve kalibrasyon görevlerine ait kayıtları görmelisiniz. `Ctrl+C` yalnız log ekranından çıkar; miner çalışmaya devam eder.

## 12. Kaydı ve kalibrasyonu kontrol edelim

İkinci terminalde FLOP kısayolunu tekrar tanımlayıp hesabı sorguluyoruz:

```sh
FLOP_CLI="$(sed -n 's/^FLOP_CLI_IMAGE=//p' .env)"
: "${FLOP_CLI:?Önce .env dosyasını kontrol edin}"
flop() {
  docker run --rm --network host -v "$PWD:/work" "$FLOP_CLI" "$@"
}
MINER_ADDRESS="$(jq -er '.address' private/miner-key.json)"
flop --network devnet --rpc wss://rpc.devnet.flop.network \
  miner status "$MINER_ADDRESS" --json
docker compose logs --tail=100 miner
```

Doğru hesapta kayıt ve stake görünmeli. JSON'da `registered` ve `calibration` alanlarına bakın; `calibration: null` ise kalibrasyon sonucu henüz yok. [Explorer → Mining → Calibration](https://explorer.devnet.flop.network/mining) ekranında kendi adresinizi bulup kabul edilmiş kapasite kaydını kontrol edin. Süreyi sabitlemeyin; ağın güncel kuralları belirler.

📌 **Kalibrasyon tamam = ücretli iş aldık demek değil.** Dış miner kabulü açıldığında gerçek iş ve finalized ödeme ayrıca doğrulanır.

## 13. Denemeyi bitirirken

Devam eden görevleri/yükümlülükleri kontrol edip kapatma zamanını planlayın. Cevapsız görev stake kesintisine yol açabilir. Servisleri durdurmak için:

```sh
docker compose stop
docker compose ps -a
```

Servisler durmuş görünür; dosyalar ve volume korunur. Bu komut **Vast faturasını durdurmaz**. Vast'ta **Stop** sonrası disk ücreti sürebilir; **Destroy** sunucuyu ve verileri siler.

**Destroy'dan önce:** `.env`, `private/` ve `worker_state` yedeğinin kendi bilgisayarınıza geçtiğini kontrol edin. Yedeği nasıl alacağınızdan emin değilseniz Destroy kullanmayın; önce yedekleme adımını tamamlayın. Ardından panelde kalan disk/instance ücretlerini kontrol edin.

## Son notlar

- **Devnet ödül ve airdrop puanı vermez.** Burdaki işlemlerin gelecekteki testnete sayılacağı garantisi yok.
- `.env`, `private/` ve `worker_state` volume'unu şifreli yedekleyin. Seed/API anahtarlarını paylaşmayın. Güncellemeden önce mevcut işleri bitirin, aynı hesap ve verileri koruyun.

Resmî kaynaklar: [FLOP miner](https://docs.flop.network/docs/run-miner) · [Release manifesti](https://flop.network/releases.json) · [Vast VM](https://docs.vast.ai/guides/instances/virtual-machines) · [NVIDIA Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) · [Cloudflare tünel sınırları](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
