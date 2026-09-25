/*OVA_SERVIDOR_RAILWAY_PARCHE*/
(function(){
try{
  var SERVIDOR = 'https://worker-production-be04.up.railway.app';
  var cacheServidor = null, cacheFecha = null;

  function fetchServidor(fechaISO){
    if(cacheServidor && cacheFecha===fechaISO) return Promise.resolve(cacheServidor);
    return fetch(SERVIDOR+'/api/analisis-hoy?fecha='+fechaISO, {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
      .then(function(d){
        cacheServidor = d; cacheFecha = fechaISO;
        return d;
      });
  }

  function americana(p){
    if(p==null||p<=0||p>=100) return '—';
    if(p>=50) return '-'+Math.round(p/(100-p)*100);
    return '+'+Math.round((100-p)/p*100);
  }

  function pickHtmlSrv(vc, pctGana, ganaName, idGana, cuotaJusta){
    var esco = (typeof escudo === 'function') ? escudo(idGana, 24) : '';
    var cg = (typeof CORTE_GRIS   !== 'undefined') ? CORTE_GRIS   : 0.50;
    var cs = (typeof CORTE_SOLIDO !== 'undefined') ? CORTE_SOLIDO : 1.20;
    var cf = (typeof CORTE_FUERTE !== 'undefined') ? CORTE_FUERTE : 1.70;
    if(vc < cg){
      return '<div class="pick no"><div class="q">Sin pick al ganador</div><div class="r">Ventaja de solo '+vc.toFixed(2)+' carreras (servidor), por debajo del corte de '+cg.toFixed(2)+'. Muy parejos.</div></div>';
    }
    if(vc >= cf){
      return '<div class="pick big"><div class="q">'+esco+'Juégale a '+ganaName+'</div><div class="r">Gran oportunidad (servidor) · '+pctGana.toFixed(1)+'% · cuota justa '+cuotaJusta+' · ventaja de '+vc.toFixed(2)+' carreras. Solo si el book paga más de '+cuotaJusta+'.</div></div>';
    }
    if(vc >= cs){
      return '<div class="pick"><div class="q">'+esco+'Juégale a '+ganaName+'</div><div class="r">Ventaja sólida (servidor) · '+pctGana.toFixed(1)+'% · cuota justa '+cuotaJusta+' · ventaja de '+vc.toFixed(2)+' carreras. Solo si el book paga más de '+cuotaJusta+'.</div></div>';
    }
    return '<div class="pick no"><div class="q">Zona gris</div><div class="r">'+ganaName+' sale arriba con '+pctGana.toFixed(1)+'% y '+vc.toFixed(2)+' carreras de ventaja (servidor), que no llega a '+cs.toFixed(2)+'. Déjalo pasar salvo que el book regale la línea.</div></div>';
  }

  function actualizarConServidor(el, j){
    var c = el._calc, d = el._d;
    if(!c || !d || !j || !j.home || !j.away) return;
    var body = el.querySelector('.body');
    if(!body) return;

    var chH = j.home.chance, chA = j.away.chance;
    if(chH == null || chA == null) return;
    var favHome  = chH >= chA;
    var pctGana  = favHome ? chH : chA;
    var ganaName = favHome ? c.nomH : c.nomA;
    var idGana   = favHome ? (d.h.team && d.h.team.id) : (d.a.team && d.a.team.id);
    var vc       = Math.abs(j.delta != null ? j.delta : 0);
    var cuotaJusta = (typeof american === 'function') ? american(pctGana) : americana(pctGana);

    var tabV = body.querySelector('.tab[data-t="v"]');
    if(!tabV) return;

    var hero = tabV.querySelector('.ovaHero');
    if(hero){
      var sides = hero.querySelectorAll('.side');
      if(sides.length === 2){
        var pcA = sides[0].querySelector('.pc');
        var pcH = sides[1].querySelector('.pc');
        if(pcA) pcA.textContent = chA.toFixed(1) + '%';
        if(pcH) pcH.textContent = chH.toFixed(1) + '%';
        sides[0].classList.toggle('fav', !favHome);
        sides[1].classList.toggle('fav', favHome);
      }
      var cxb = hero.querySelector('.cx b');
      if(cxb) cxb.textContent = cuotaJusta;
      var cap = hero.querySelector('.cap');
      if(cap){
        var cg2 = (typeof CORTE_GRIS !== 'undefined') ? CORTE_GRIS : 0.50;
        cap.innerHTML = (vc < cg2)
          ? 'Muy parejo (servidor): ventaja de solo <b>' + vc.toFixed(2) + '</b> carreras'
          : '<b>' + ganaName + '</b> favorito (servidor) · ventaja de <b>' + vc.toFixed(2) + '</b> carreras';
      }
    }

    var kpi = tabV.querySelector('.kpi');
    if(kpi){
      var divs = kpi.querySelectorAll('div');
      if(divs[0]){
        var b0 = divs[0].querySelector('b'), s0 = divs[0].querySelector('span');
        if(b0) b0.textContent = pctGana.toFixed(1) + '%';
        if(s0) s0.textContent = ganaName + ' gana';
      }
      if(divs[1]){
        var b1 = divs[1].querySelector('b');
        if(b1) b1.textContent = cuotaJusta;
      }
      if(divs[2] && j.total && j.total.esperado != null){
        var b2 = divs[2].querySelector('b');
        if(b2) b2.textContent = j.total.esperado.toFixed(1);
      }
    }

    var pickHtml = pickHtmlSrv(vc, pctGana, ganaName, idGana, cuotaJusta);
    body.querySelectorAll('.pickbox').forEach(function(pb){ pb.innerHTML = pickHtml; });
  }

  function pintarComparacion(el){
    var body = el.querySelector('.body');
    if(!body) return;
    var tabV = body.querySelector('.tab[data-t="v"]');
    if(!tabV) return;

    var existente = tabV.querySelector('.srv-compara');
    if(existente) existente.remove();

    var fe = document.getElementById('fecha');
    var fechaISO = (fe && fe.value) || (new Date()).toISOString().slice(0,10);

    var caja = document.createElement('div');
    caja.className = 'srv-compara';
    caja.style.cssText = 'background:#131D2C;border:1px solid #2A3E56;border-left:3px solid #22D3EE;border-radius:10px;padding:12px 14px;margin:14px 0;font-size:12.5px;color:#9DB2C8;line-height:1.6';
    caja.innerHTML = '<b style="color:#F1F5FB">☁️ Servidor Railway</b><br>Buscando...';
    tabV.insertBefore(caja, tabV.firstChild);

    fetchServidor(fechaISO).then(function(d){
      var juegos = (d && d.juegos) || [];
      var pk = el._pk;
      var j = null;
      for(var i=0;i<juegos.length;i++){ if(String(juegos[i].gamePk)===String(pk)){ j=juegos[i]; break; } }
      if(!j){
        caja.innerHTML = '<b style="color:#F1F5FB">☁️ Servidor Railway</b><br>Este juego todavía no está calculado ahí (puede que falte lineup confirmado o que el reloj no haya pasado por él todavía). Se muestran los números propios de OVA.';
        return;
      }
      actualizarConServidor(el, j);
      var h = j.home, a = j.away;
      var t = j.total || {};
      var pon = j.ponches || {};
      var txt = '<b style="color:#F1F5FB">☁️ Servidor Railway</b> <span style="color:#6D8299">(ya aplicado arriba en el Veredicto)</span><br>';
      txt += (h?h.nombre:'Local')+': <b style="color:#F1F5FB">'+(h?h.chance:'—')+'%</b> ('+americana(h?h.chance:null)+') · ';
      txt += (a?a.nombre:'Visita')+': <b style="color:#F1F5FB">'+(a?a.chance:'—')+'%</b> ('+americana(a?a.chance:null)+')<br>';
      if(t.esperado!=null) txt += 'Total esperado: <b style="color:#F1F5FB">'+t.esperado+'</b> · ';
      if(t.carreras_esperadas) txt += 'carreras: '+t.carreras_esperadas.away+' / '+t.carreras_esperadas.home+'<br>';
      if(pon.away!=null || pon.home!=null) txt += 'Ponches esperados: '+(pon.away!=null?pon.away:'—')+' / '+(pon.home!=null?pon.home:'—');
      caja.innerHTML = txt;
    }).catch(function(e){
      caja.innerHTML = '<b style="color:#F1F5FB">☁️ Servidor Railway</b><br><span style="color:#F87171">No se pudo conectar (' + e.message + '). Revisa tu internet o si el servidor está caido. Se muestran los números propios de OVA.</span>';
    });
  }

  function esperarYPintar(el){
    if(typeof el._cargar !== 'function') return;
    el._cargar().then(function(){
      setTimeout(function(){ pintarComparacion(el); }, 50);
    });
  }

  if(window.MutationObserver){
    new MutationObserver(function(muts){
      muts.forEach(function(m){
        if(m.type==='attributes' && m.attributeName==='class'){
          var el = m.target;
          if(el.classList && el.classList.contains('game') && el.classList.contains('open')){
            esperarYPintar(el);
          }
        }
      });
    }).observe(document.body, {attributes:true, subtree:true, attributeFilter:['class']});
  }
}catch(e){ console.error('parche servidor railway:', e); }
})();
