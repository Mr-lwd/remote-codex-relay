// Recognize TeX delimiters before Markdown consumes backslashes as escapes.
// Using tokenizer constructs keeps code, URLs, and escaped examples untouched.
export default function remarkRelayMath() {
  const data = this.data();
  (data.micromarkExtensions ||= []).push({
    flow: {
      92: { tokenize: tokenizer(true, false), concrete: true },
      36: { tokenize: tokenizer(true, true), concrete: true },
    },
    text: {
      92: { tokenize: tokenizer(false, false) },
      36: {
        tokenize: tokenizer(false, true),
        previous(code) {
          return code !== 36 || this.events[this.events.length - 1][1].type === 'characterEscape';
        },
      },
    },
  });
  (data.fromMarkdownExtensions ||= []).push({
    enter: { relayMath: enterMath },
    exit: { relayMath: exitMath },
  });
}

function lineEnding(code) {
  return code === -5 || code === -4 || code === -3;
}

function tokenizer(flow, dollars) {
  return function (effects, ok, nok) {
    const self = this;
    let token;
    let closing;
    let display = dollars;
    let dataOpen = false;
    return start;

    function start(code) {
      // Do not split an existing paragraph: it may contain a multiline code
      // span. Text constructs still recognize formulas following prose.
      if (flow && self.interrupt) return nok(code);
      token = effects.enter('relayMath');
      effects.enter('relayMathMarker');
      effects.consume(code);
      return open;
    }

    function open(code) {
      if (dollars ? code !== 36 : code !== 91 && (flow || code !== 40)) return nok(code);
      display ||= code === 91;
      closing = dollars ? 36 : display ? 93 : 41;
      token._relayFlow = flow;
      token._relayDisplay = display;
      effects.consume(code);
      effects.exit('relayMathMarker');
      return first;
    }

    function first(code) {
      // Longer dollar fences remain the responsibility of remark-math.
      return dollars && code === 36 ? nok(code) : inside(code);
    }

    function inside(code) {
      if (code === null) return nok(code);
      if (!lineEnding(code) && !dataOpen) {
        effects.enter('relayMathData');
        dataOpen = true;
      }
      if (code === 92) {
        effects.consume(code);
        return escaped;
      }
      if (dollars && code === 36) {
        effects.consume(code);
        return close;
      }
      if (lineEnding(code)) {
        endData();
        effects.enter('lineEnding');
        effects.consume(code);
        effects.exit('lineEnding');
        return nextLine;
      } else {
        effects.consume(code);
      }
      return inside;
    }

    function nextLine(code) {
      // A quote/list must not absorb content outside its Markdown container.
      if (flow && self.parser.lazy[self.now().line]) return nok(code);
      return inside(code);
    }

    function escaped(code) {
      if (!dollars && code === closing) return close(code);
      if (code === null || lineEnding(code)) return inside(code);
      // Consume escaped backslashes/dollars together: \\] is not a closing \].
      effects.consume(code);
      return inside;
    }

    function close(code) {
      if (code !== closing) return inside(code);
      effects.consume(code);
      return closed;
    }

    function closed(code) {
      if (dollars && code === 36) return nok(code);
      return flow ? after(code) : done(code);
    }

    function after(code) {
      if (code === 32 || code === -2 || code === -1) {
        effects.consume(code);
        return after;
      }
      return code === null || lineEnding(code) ? done(code) : nok(code);
    }

    function done(code) {
      endData();
      effects.exit('relayMath');
      return ok(code);
    }

    function endData() {
      if (dataOpen) effects.exit('relayMathData');
      dataOpen = false;
    }
  };
}

function enterMath(token) {
  this.enter({ type: token._relayFlow ? 'math' : 'inlineMath', value: '' }, token);
  this.buffer();
}

function exitMath(token) {
  this.resume();
  const node = this.stack[this.stack.length - 1];
  const source = this.sliceSerialize(token).trimEnd();
  node.value = source.slice(2, -2).trim();
  const code = {
    type: 'element',
    tagName: 'code',
    properties: {
      className: ['language-math', token._relayDisplay ? 'math-display' : 'math-inline'],
    },
    children: [{ type: 'text', value: node.value }],
  };
  node.data = token._relayFlow
    ? { hName: 'pre', hChildren: [code] }
    : { hName: code.tagName, hProperties: code.properties, hChildren: code.children };
  this.exit(token);
}
