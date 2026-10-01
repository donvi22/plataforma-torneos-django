(function () {
    const form = document.querySelector('.tournament-form');
    if (!form) return;

    const videojuego = document.getElementById('id_videojuego');
    const rangoMinimo = document.getElementById('id_rango_minimo');
    const rangoMaximo = document.getElementById('id_rango_maximo');
    const formato = document.getElementById('id_formato_competitivo');
    const maxParticipantes = document.getElementById('id_max_participantes');
    const tipo = document.getElementById('id_tipo');
    const apertura = document.getElementById('id_fecha_apertura_inscripciones');
    const cierre = document.getElementById('id_fecha_cierre_inscripciones');
    const inicio = document.getElementById('id_fecha_inicio_prevista');
    const prorroga = document.getElementById('id_duracion_prorroga_min');
    const status = document.getElementById('rangos-status');
    const rangosUrl = form.dataset.rangosUrl;
    const tamanos = JSON.parse(document.getElementById('formato-tamanos-data').textContent);

    function actualizarTamanos() {
        const actuales = tamanos[formato.value] || [];
        const seleccionado = maxParticipantes.value;
        maxParticipantes.innerHTML = '';
        actuales.forEach(function (valor) {
            const opcion = new Option(valor, valor, false, String(valor) === seleccionado);
            maxParticipantes.add(opcion);
        });
        if (!actuales.some(function (valor) { return String(valor) === seleccionado; }) && actuales.length) {
            maxParticipantes.value = actuales[0];
        }
    }

    function llenarRangos(rangos, conservar) {
        [rangoMinimo, rangoMaximo].forEach(function (campo) {
            const anterior = conservar ? campo.value : '';
            campo.innerHTML = '';
            campo.add(new Option('Sin rango declarado', ''));
            rangos.forEach(function (rango) {
                campo.add(new Option(rango.posicion + '. ' + rango.nombre, rango.id, false, String(rango.id) === anterior));
            });
        });
        status.textContent = rangos.length ? '' : 'Este videojuego no tiene rangos configurados.';
    }

    function actualizarRangos(conservar) {
        if (!videojuego.value) {
            llenarRangos([], false);
            return;
        }
        fetch(rangosUrl.replace('/0/', '/' + videojuego.value + '/'), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (respuesta) { return respuesta.ok ? respuesta.json() : { rangos: [] }; })
            .then(function (datos) { llenarRangos(datos.rangos, conservar); })
            .catch(function () { status.textContent = 'No se pudieron cargar los rangos. Puedes continuar sin declararlos.'; });
    }

    function fechaValida(valor) { return valor ? new Date(valor) : null; }
    function formatoFecha(fecha) {
        const pad = function (numero) { return String(numero).padStart(2, '0'); };
        return fecha.getFullYear() + '-' + pad(fecha.getMonth() + 1) + '-' + pad(fecha.getDate()) + 'T' + pad(fecha.getHours()) + ':' + pad(fecha.getMinutes());
    }
    function coordinarFechas() {
        const aperturaFecha = fechaValida(apertura.value);
        if (!aperturaFecha) return;
        const tipoPublico = tipo.value === 'PUBLICO';
        const minutos = tipoPublico ? 15 : 0;
        const cierreFecha = fechaValida(cierre.value);
        const cierreMinimo = new Date(aperturaFecha.getTime() + minutos * 60000);
        cierre.min = formatoFecha(aperturaFecha);
        if (!cierreFecha || (tipoPublico && cierreFecha < cierreMinimo) || (!tipoPublico && cierreFecha < aperturaFecha)) {
            cierre.value = formatoFecha(cierreMinimo);
            if (tipoPublico) status.textContent = 'El cierre se ha ajustado al mínimo permitido de 15 minutos.';
        }
        const cierreActual = fechaValida(cierre.value);
        if (cierreActual) {
            const referencia = new Date(cierreActual.getTime() + (tipoPublico ? Number(prorroga.value || 0) : 0) * 60000);
            inicio.min = formatoFecha(referencia);
            const inicioFecha = fechaValida(inicio.value);
            if (!inicioFecha || inicioFecha < referencia) inicio.value = formatoFecha(referencia);
        }
    }

    videojuego.addEventListener('change', function () { actualizarRangos(false); });
    formato.addEventListener('change', actualizarTamanos);
    apertura.addEventListener('change', coordinarFechas);
    cierre.addEventListener('change', coordinarFechas);
    prorroga.addEventListener('change', coordinarFechas);
    actualizarTamanos();
    actualizarRangos(true);
    coordinarFechas();
}());