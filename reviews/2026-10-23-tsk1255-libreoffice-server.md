# tsk-1255 поставка 2 — LibreOffice на lms-spw-vds (/server-admin)

Сервер: lms-spw-vds (Ubuntu 22.04.5, 2 CPU, 3.9 ГБ RAM, swap 2 ГБ, диск 49 ГБ / занято 24 %).
Режим: build на боевом сервере, только новые пакеты (0 upgraded), без перезапуска служб.

## Изменения
- Пакеты: libreoffice-impress (обычный, не nogui), poppler-utils, libpaper-utils,
  fonts-liberation2, fonts-crosextra-carlito, fonts-crosextra-caladea + зависимости.
- Вариант libreoffice-impress-nogui 7.3.7 НЕ открывает pptx (выход 1 без сообщения, под app —
  Signal 6), writer-nogui при этом работает. Заменён на обычный libreoffice-impress.
- Установка с NEEDRESTART_MODE=l: службы lms/spw/lms-pilot/spw-pilot не перезапускались
  (ActiveEnterTimestamp до и после совпадает с выкатами 09:13/09:16 и 14.10).
- Снимок: /root/bak-tsk1255/dpkg-selections.before, журнал apt — /root/bak-tsk1255/apt-install.log.

Разница пакетов: >fontconfig >fonts-crosextra-caladea >fonts-crosextra-carlito >fonts-liberation2 >fonts-opensymbol >libabsl20210324:amd64 >libabw-0.1-1:amd64 >libavahi-client3:amd64 >libavahi-common-data:amd64 >libavahi-common3:amd64 >libboost-filesystem1.74.0:amd64 >libboost-iostreams1.74.0:amd64 >libboost-locale1.74.0:amd64 >libboost-thread1.74.0:amd64 >libcairo2:amd64 >libcdr-0.1-1:amd64 >libclucene-contribs1v5:amd64 >libclucene-core1v5:amd64 >libcups2:amd64 >libdconf1:amd64 >libe-book-0.1-1:amd64 >libeot0:amd64 >libepoxy0:amd64 >libepubgen-0.1-1:amd64 >libetonyek-0.1-1:amd64 >libexttextcat-2.0-0:amd64 >libexttextcat-data >libfreehand-0.1-1 >libgpgmepp6:amd64 >libgraphite2-3:amd64 >libgstreamer-plugins-base1.0-0:amd64 >libharfbuzz-icu0:amd64 >libharfbuzz0b:amd64 >libhunspell-1.7-0:amd64 >libhyphen0:amd64 >libice6:amd64 >liblangtag-common >liblangtag1:amd64 >liblcms2-2:amd64 >libmhash2:amd64 >libmspub-0.1-1:amd64 >libmwaw-0.3-3:amd64 >libmythes-1.2-0:amd64 >libodfgen-0.1-1:amd64 >libopenjp2-7:amd64 >liborc-0.4-0:amd64 >liborcus-0.17-0:amd64 >liborcus-parser-0.17-0:amd64 >libpagemaker-0.0-0:amd64 >libpaper-utils >libpaper1:amd64 >libpixman-1-0:amd64 >libpoppler118:amd64 >libraptor2-0:amd64 >librasqal3:amd64 >librdf0:amd64 >libreoffice-base-core >libreoffice-common >libreoffice-core >libreoffice-draw >libreoffice-draw-nogui >libreoffice-impress >libreoffice-impress-nogui >libreoffice-style-colibre >libreoffice-writer-nogui >librevenge-0.0-0:amd64 >libsm6:amd64 >libuno-cppu3 >libuno-cppuhelpe

## Проверки
- Под app: tiny.pptx → pdf OK; урок 6 (4 МБ, 27 слайдов) → pdf за 23 с, пик RSS 290 МБ, nice 15.
- Свободно после: ~1.75 ГБ available.

## Откат
`apt-get remove libreoffice-impress libreoffice-core libreoffice-draw libreoffice-writer-nogui poppler-utils`
(полный список — diff dpkg-selections). Код LMS при отсутствии soffice отдаёт задаче статус failed.

## Риски
Память: конвертации идут по одной (семафор в процессе), nice 15, таймаут 15 мин.
