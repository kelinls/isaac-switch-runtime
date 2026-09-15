// Import Nintendo Switch NRO segments and ELF dynamic symbols into a raw Ghidra program.
// @category Nintendo Switch

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;

public class ImportNroSymbols extends GhidraScript {
    private long u32(long offset) throws Exception {
        byte[] bytes = new byte[4];
        currentProgram.getMemory().getBytes(toAddr(offset), bytes);
        return Integer.toUnsignedLong(ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getInt());
    }

    private long u64(long offset) throws Exception {
        byte[] bytes = new byte[8];
        currentProgram.getMemory().getBytes(toAddr(offset), bytes);
        return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getLong();
    }

    private int u8(long offset) throws Exception {
        return Byte.toUnsignedInt(currentProgram.getMemory().getByte(toAddr(offset)));
    }

    private String cstring(long offset) throws Exception {
        StringBuilder value = new StringBuilder();
        Memory memory = currentProgram.getMemory();
        for (long cursor = offset; cursor < offset + 0x10000; cursor++) {
            int character = Byte.toUnsignedInt(memory.getByte(toAddr(cursor)));
            if (character == 0) {
                return value.toString();
            }
            value.append((char) character);
        }
        throw new IllegalArgumentException("Unterminated symbol name at 0x" + Long.toHexString(offset));
    }

    private void splitAndProtectSegments() throws Exception {
        Memory memory = currentProgram.getMemory();
        MemoryBlock block = memory.getBlock(toAddr(0));
        long rodataOffset = u32(0x28);
        long dataOffset = u32(0x30);

        if (!block.getStart().equals(toAddr(rodataOffset)) && block.contains(toAddr(rodataOffset))) {
            memory.split(block, toAddr(rodataOffset));
        }
        MemoryBlock rodata = memory.getBlock(toAddr(rodataOffset));
        if (!rodata.getStart().equals(toAddr(dataOffset)) && rodata.contains(toAddr(dataOffset))) {
            memory.split(rodata, toAddr(dataOffset));
        }
        MemoryBlock data = memory.getBlock(toAddr(dataOffset));
        block.setName(".text");
        rodata.setName(".rodata");
        data.setName(".data");

        block.setRead(true);
        block.setWrite(false);
        block.setExecute(true);
        rodata.setRead(true);
        rodata.setWrite(false);
        rodata.setExecute(false);
        data.setRead(true);
        data.setWrite(true);
        data.setExecute(false);
    }

    @Override
    public void run() throws Exception {
        if (u32(0x10) != 0x304f524eL) {
            throw new IllegalArgumentException("Current program does not contain an NRO0 header");
        }

        splitAndProtectSegments();

        long mod0 = u32(0x4);
        if (u32(mod0) != 0x30444f4dL) {
            throw new IllegalArgumentException("NRO MOD0 header not found");
        }

        long dynamic = mod0 + u32(mod0 + 4);
        long symtab = 0;
        long strtab = 0;
        long hash = 0;
        long syment = 24;
        for (long cursor = dynamic; ; cursor += 16) {
            long tag = u64(cursor);
            long value = u64(cursor + 8);
            if (tag == 0) {
                break;
            }
            if (tag == 4) hash = value;
            if (tag == 5) strtab = value;
            if (tag == 6) symtab = value;
            if (tag == 11) syment = value;
        }
        if (symtab == 0 || strtab == 0 || hash == 0 || syment < 24) {
            throw new IllegalArgumentException("Incomplete NRO dynamic symbol metadata");
        }

        long symbolCount = u32(hash + 4);
        FunctionManager functions = currentProgram.getFunctionManager();
        int importedFunctions = 0;
        int importedLabels = 0;
        for (long index = 1; index < symbolCount && !monitor.isCancelled(); index++) {
            long entry = symtab + index * syment;
            long nameOffset = u32(entry);
            int info = u8(entry + 4);
            int section = (int) u32(entry + 6) & 0xffff;
            long value = u64(entry + 8);
            if (section == 0 || value == 0 || nameOffset == 0) {
                continue;
            }

            String name = cstring(strtab + nameOffset);
            if (name.isEmpty()) {
                continue;
            }
            Address address = toAddr(value);
            try {
                Symbol symbol = currentProgram.getSymbolTable().createLabel(address, name, SourceType.IMPORTED);
                if (symbol != null && currentProgram.getSymbolTable().getPrimarySymbol(address) == null) {
                    symbol.setPrimary();
                }
                importedLabels++;
                if ((info & 0xf) == 2) {
                    Function function = functions.getFunctionAt(address);
                    if (function == null) {
                        function = createFunction(address, name);
                    } else if (function.getSymbol().getSource() == SourceType.DEFAULT) {
                        function.setName(name, SourceType.IMPORTED);
                    }
                    importedFunctions++;
                }
            } catch (Exception exception) {
                printerr("Skipping symbol " + name + " at " + address + ": " + exception.getMessage());
            }
        }
        println("Imported " + importedLabels + " labels and " + importedFunctions + " function symbols");
    }
}
